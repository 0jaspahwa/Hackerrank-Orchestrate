"""`financial_events.csv` -> an opening balance plus dated cash flows in the
user's home currency.

This module decides which rows move cash, when, in which direction, and how much
of the future to project. It does NOT decide affordability -- that is
`capacity.py`, which consumes a `Ledger` and nothing else.

The projection rule is a PARAMETER (`ProjectionRule`), not a hardcode, because
recurrence is not supplied by the dataset and is the dominant score driver.
`evaluation/search_projection.py` sweeps it.

Every `CashFlow` carries provenance -- which event produced it, why it was
included, and whether it was observed or projected. Every dropped event is
recorded with a reason. The trace is a deliverable, not a debug aid.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from statistics import median
from typing import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.config import ForecastConfig, FxConfig, TransferConfig
from src.contract import to_money
from src.fx import FXError, FXFallback, RateTable, convert_with_trace

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class EventStatus(StrEnum):
    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNREALIZED = "unrealized"


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"
    NON_CASH = "non_cash"


class FinancialEvent(BaseModel):
    """One row of `financial_events.csv`. `amount` is None for the 16 blanks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: Direction
    amount: Decimal | None
    currency: str
    event_date: date
    settlement_date: date | None
    status: EventStatus
    linked_event_id: str | None
    flexibility: str
    minimum_allowed_amount: Decimal | None

    @field_validator("amount", "minimum_allowed_amount", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> Decimal | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return to_money(v)  # type: ignore[arg-type]

    @field_validator("linked_event_id", mode="before")
    @classmethod
    def _blank_link(cls, v: object) -> str | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return str(v).strip()

    @field_validator("settlement_date", mode="before")
    @classmethod
    def _blank_date(cls, v: object) -> date | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v if isinstance(v, date) else date.fromisoformat(str(v).strip())

    @property
    def cash_date(self) -> date:
        """When the money actually moves: settlement when known, else event date."""
        return self.settlement_date or self.event_date

    @property
    def signed_amount(self) -> Decimal | None:
        """Positive for credits, negative for debits, None when blank."""
        if self.amount is None:
            return None
        return -self.amount if self.direction is Direction.DEBIT else self.amount


class Profile(BaseModel):
    """One row of `financial_profiles.csv`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: tuple[str, ...] = ()
    expense_categories_to_protect: tuple[str, ...] = ()
    expense_categories_user_is_willing_to_reduce: tuple[str, ...] = ()
    expense_categories_user_is_willing_to_stop: tuple[str, ...] = ()
    payment_methods_user_will_consider: tuple[str, ...] = ()
    max_installment_months: int | None = None


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class InclusionReason(StrEnum):
    """Why a cash flow is in the ledger."""

    PENDING_DEBIT = "pending_debit"
    SCHEDULED_FUTURE = "scheduled_future"
    PROJECTED_RECURRING = "projected_recurring"
    PROJECTED_BASELINE = "projected_baseline"


class ExclusionReason(StrEnum):
    """Why an event produced no cash flow."""

    STATUS_EXCLUDED = "status_excluded"
    NON_CASH = "non_cash"
    PENDING_CREDIT = "pending_credit"
    INTERNAL_TRANSFER = "internal_transfer"
    LIFECYCLE_CHAIN = "lifecycle_chain"
    BLANK_AMOUNT = "blank_amount"
    SETTLED_HISTORY = "settled_history"
    OUTSIDE_WINDOW = "outside_window"
    NOT_SELECTED_FOR_PROJECTION = "not_selected_for_projection"


class CashFlow(BaseModel):
    """A dated, signed movement in the user's home currency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_date: date
    #: Signed: negative is money out.
    amount: Decimal
    source_event_id: str
    reason: InclusionReason
    projected: bool
    description: str = ""
    category: str = ""
    original_amount: Decimal | None = None
    original_currency: str = ""


class ExcludedEvent(BaseModel):
    """An event that produced no cash flow, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    reason: ExclusionReason
    detail: str = ""


class Ledger(BaseModel):
    """Opening balance plus every dated flow inside the forecast window.

    `flows` is sorted by date. Everything is in `home_currency`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    request_date: date
    window_end: date
    home_currency: str
    opening_balance: Decimal
    flows: tuple[CashFlow, ...] = ()
    #: Event ids whose amount is blank and for which no override was supplied.
    missing_amounts: tuple[str, ...] = ()
    excluded: tuple[ExcludedEvent, ...] = ()
    fx_fallbacks: tuple[str, ...] = ()
    fx_errors: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The projection rule
# ---------------------------------------------------------------------------


class SelectorFamily(StrEnum):
    TRAILING = "trailing"
    DAY_STABLE = "day_stable"
    DESC_ANY = "desc_any"
    CAT_STABLE = "cat_stable"
    HYBRID = "hybrid"


class SelectorSpec(BaseModel):
    """A selector name such as `trailing_31` or `day_stable_2`, parsed.

    The trailing number means days for `trailing_*` and a minimum occurrence
    count for the series selectors, which is why it is parsed once here rather
    than re-read in a branch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: SelectorFamily
    trailing_days: int = 0
    min_occurrences: int = 0

    @classmethod
    def parse(cls, name: str) -> "SelectorSpec":
        cleaned = name.strip().lower()
        if cleaned == SelectorFamily.HYBRID.value:
            return cls(family=SelectorFamily.HYBRID)
        prefix, _, suffix = cleaned.rpartition("_")
        if not suffix.isdigit():
            raise ValueError(f"unknown selector {name!r}")
        number = int(suffix)
        if prefix == SelectorFamily.TRAILING.value:
            return cls(family=SelectorFamily.TRAILING, trailing_days=number)
        if prefix == SelectorFamily.DAY_STABLE.value:
            return cls(family=SelectorFamily.DAY_STABLE, min_occurrences=number)
        if prefix == SelectorFamily.DESC_ANY.value:
            return cls(family=SelectorFamily.DESC_ANY, min_occurrences=number)
        if prefix == SelectorFamily.CAT_STABLE.value:
            return cls(family=SelectorFamily.CAT_STABLE, min_occurrences=number)
        raise ValueError(f"unknown selector {name!r}")


class AmountMode(StrEnum):
    LAST = "last"
    MEAN = "mean"
    MEDIAN = "median"


class IncomeMode(StrEnum):
    """How much future income the projection is willing to invent.

    AGENTS.md 6.3: "Do not invent unsupported future income." Expenses and
    income are NOT symmetric -- a missed expense is a dangerous recommendation,
    a missed credit is only a cautious one -- so how income is projected is its
    own axis rather than a side effect of the selector.
    """

    #: Project credits exactly like debits.
    ALL = "all"
    #: Project no credits at all. Only explicitly scheduled or pending income counts.
    NONE = "none"
    #: Project only credits that form a stable monthly series on a fixed day.
    #: Keeps a regular payroll, drops irregular gig and platform payouts.
    STABLE_ONLY = "stable"


class ProjectionRule(BaseModel):
    """How historical spending is replayed into the future.

    Frozen and hashable so a rule can key a results table in the rule search.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: One of trailing_N, day_stable_K, desc_any_K, cat_stable_K, hybrid.
    selector: str
    amount_mode: AmountMode = AmountMode.LAST
    horizon_months: int = 3
    #: Do flows landing exactly on request_date count toward the balance path.
    include_request_date: bool = False
    #: How much future income may be projected. See `IncomeMode`.
    income_mode: IncomeMode = IncomeMode.ALL

    @property
    def spec(self) -> SelectorSpec:
        return SelectorSpec.parse(self.selector)

    def label(self) -> str:
        flag = "incl_t" if self.include_request_date else "excl_t"
        return (
            f"{self.selector}/{self.amount_mode.value}/h{self.horizon_months}"
            f"/{flag}/inc:{self.income_mode.value}"
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _split_pipe(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split("|") if part.strip())


def load_events(path: str | Path) -> tuple[FinancialEvent, ...]:
    """Read `financial_events.csv`."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return tuple(FinancialEvent(**row) for row in csv.DictReader(handle))


def load_profiles(path: str | Path) -> dict[str, Profile]:
    """Read `financial_profiles.csv`, keyed by user_id."""
    profiles: dict[str, Profile] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            months = (row.get("max_installment_months") or "").strip()
            profiles[row["user_id"]] = Profile(
                user_id=row["user_id"],
                home_currency=row["home_currency"].strip().upper(),
                current_available_balance=to_money(row["current_available_balance"]),
                minimum_balance_to_keep=to_money(row["minimum_balance_to_keep"]),
                financial_priorities=_split_pipe(row.get("financial_priorities", "")),
                expense_categories_to_protect=_split_pipe(
                    row.get("expense_categories_to_protect", "")
                ),
                expense_categories_user_is_willing_to_reduce=_split_pipe(
                    row.get("expense_categories_user_is_willing_to_reduce", "")
                ),
                expense_categories_user_is_willing_to_stop=_split_pipe(
                    row.get("expense_categories_user_is_willing_to_stop", "")
                ),
                payment_methods_user_will_consider=_split_pipe(
                    row.get("payment_methods_user_will_consider", "")
                ),
                max_installment_months=int(months) if months else None,
            )
    return profiles


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def add_months(anchor: date, months: int) -> date:
    """Add whole months, clamping the day to the target month's length.

    31 January + 1 month is 28 (or 29) February, not an error.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def _on_day_of_month(anchor: date, day: int) -> date:
    return anchor.replace(day=min(day, calendar.monthrange(anchor.year, anchor.month)[1]))


# ---------------------------------------------------------------------------
# Stage 1 -- filtering
# ---------------------------------------------------------------------------


def collapse_chains(events: Sequence[FinancialEvent]) -> tuple[frozenset[str], list[ExcludedEvent]]:
    """Find the event ids belonging to a `linked_event_id` lifecycle.

    A chain is one transaction's life -- purchase then refund, pending then
    settled, contribution then valuation -- so it is a ONE-OFF, never a
    recurring commitment. Both legs are withheld from projection; the settled
    legs are already netted into the opening balance, and replaying a refund
    monthly would invent income that does not exist.
    """
    by_id = {event.event_id: event for event in events}
    chained: set[str] = set()
    excluded: list[ExcludedEvent] = []

    for event in events:
        if event.linked_event_id is None:
            continue
        parent = by_id.get(event.linked_event_id)
        if parent is None:
            continue
        for member, role in ((event, "child"), (parent, "parent")):
            if member.event_id in chained:
                continue
            chained.add(member.event_id)
            excluded.append(
                ExcludedEvent(
                    event_id=member.event_id,
                    reason=ExclusionReason.LIFECYCLE_CHAIN,
                    detail=f"{role} of lifecycle {parent.event_id}->{event.event_id}",
                )
            )
    return frozenset(chained), excluded


def detect_internal_transfers(
    events: Sequence[FinancialEvent],
    *,
    chained: frozenset[str],
    transfer: TransferConfig,
    forecast: ForecastConfig,
) -> tuple[frozenset[str], list[ExcludedEvent]]:
    """Equal-magnitude debit/credit pairs a few days apart, for the same user.

    These are money moving between two accounts the same person owns. Counting
    either leg invents a dip (or a lift) that never happened, and replaying one
    monthly compounds it.

    Members of a `linked_event_id` lifecycle are skipped: an expense and its
    refund are also equal and opposite, but `collapse_chains` already owns them
    and they are not transfers.
    """
    # Only events that actually move cash can form a transfer. Without this, a
    # pending credit -- which never lands -- could pair with a real debit and
    # silently suppress it.
    candidates = [
        e
        for e in events
        if e.amount is not None
        and e.event_id not in chained
        and _status_exclusion(e, forecast) is None
    ]
    debits = [e for e in candidates if e.direction is Direction.DEBIT]
    credits = [e for e in candidates if e.direction is Direction.CREDIT]

    paired: set[str] = set()
    excluded: list[ExcludedEvent] = []
    for debit in debits:
        if debit.event_id in paired:
            continue
        for credit in credits:
            if credit.event_id in paired:
                continue
            assert debit.amount is not None and credit.amount is not None
            if abs(debit.amount - credit.amount) > transfer.amount_tolerance:
                continue
            if debit.currency != credit.currency:
                continue
            if abs((debit.cash_date - credit.cash_date).days) > transfer.window_days:
                continue
            paired.update({debit.event_id, credit.event_id})
            detail = f"matched {debit.event_id}/{credit.event_id} at {debit.amount}"
            excluded.append(
                ExcludedEvent(
                    event_id=debit.event_id,
                    reason=ExclusionReason.INTERNAL_TRANSFER,
                    detail=detail,
                )
            )
            excluded.append(
                ExcludedEvent(
                    event_id=credit.event_id,
                    reason=ExclusionReason.INTERNAL_TRANSFER,
                    detail=detail,
                )
            )
            break
    return frozenset(paired), excluded


def _status_exclusion(event: FinancialEvent, forecast: ForecastConfig) -> ExclusionReason | None:
    """Rule 2: statuses and directions that never move cash."""
    if event.status.value in forecast.excluded_statuses:
        return ExclusionReason.STATUS_EXCLUDED
    if event.direction.value == forecast.non_cash_direction:
        return ExclusionReason.NON_CASH
    if event.status is EventStatus.PENDING and event.direction is Direction.CREDIT:
        return ExclusionReason.PENDING_CREDIT
    return None


# ---------------------------------------------------------------------------
# Stage 2 -- currency normalisation
# ---------------------------------------------------------------------------


class _Normalised(BaseModel):
    """An event's amount expressed in the user's home currency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: FinancialEvent
    home_amount: Decimal
    fallbacks: tuple[FXFallback, ...] = ()


def _normalise(
    event: FinancialEvent,
    amount: Decimal,
    home_currency: str,
    rates: RateTable,
    fx: FxConfig,
) -> _Normalised:
    """Convert one event's amount into the home currency at its own cash date.

    Conversion happens HERE, at ingestion. A USD 1,800 payroll credit added raw
    to an IDR balance corrupts every downstream number.
    """
    result = convert_with_trace(
        amount,
        event.currency,
        home_currency,
        event.cash_date,
        rates,
        max_hops=fx.max_conversion_hops,
    )
    return _Normalised(event=event, home_amount=result.amount, fallbacks=result.fallbacks)


# ---------------------------------------------------------------------------
# Stage 3 -- recurrence projection
# ---------------------------------------------------------------------------


class _Series(BaseModel):
    """A group of historical events treated as one recurring commitment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    day_of_month: int
    amounts: tuple[Decimal, ...]
    months_seen: int
    representative_event_id: str
    description: str
    category: str
    #: Cash date of the most recent occurrence. Drives the recency guard.
    last_seen: date
    #: Measured cadence in days, or the configured default when unmeasurable.
    cycle_days: int


def _measure_cycle_days(dates: Sequence[date], forecast: ForecastConfig) -> int:
    """Median gap between consecutive occurrences, or the configured default.

    Measured rather than assumed so a weekly payout is judged on a weekly clock
    and a monthly subscription on a monthly one.
    """
    if len(dates) < 2:
        return forecast.default_cycle_days
    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:]) if (b - a).days > 0]
    if not gaps:
        return forecast.default_cycle_days
    return max(1, int(median(sorted(gaps))))


def is_series_current(
    last_seen: date,
    cycle_days: int,
    request_date: date,
    forecast: ForecastConfig,
) -> bool:
    """THE RECENCY GUARD.

    A series is projectable only while it is still running. If its most recent
    occurrence is further back than `recency_cycles` of its own cadence, the
    series has already missed a slot and is treated as ended.

    This is deliberately symmetric across income and expense. A cancelled
    subscription and a job that ended are the same phenomenon, and guessing that
    either continues is how a forecast goes wrong.
    """
    if forecast.recency_cycles is None:
        return True
    allowed = float(cycle_days) * forecast.recency_cycles
    return (request_date - last_seen).days <= allowed


def _pick_amount(amounts: Sequence[Decimal], mode: AmountMode) -> Decimal:
    if mode is AmountMode.LAST:
        return amounts[-1]
    if mode is AmountMode.MEAN:
        return sum(amounts, Decimal(0)) / Decimal(len(amounts))
    return Decimal(str(median(sorted(amounts))))


def _build_series(
    rows: Sequence[_Normalised],
    *,
    key_of,
    day_of,
    min_months: int,
    require_distinct_months: bool,
    forecast: ForecastConfig,
) -> list[_Series]:
    """Group normalised history into candidate recurring series."""
    grouped: dict[object, list[_Normalised]] = {}
    for row in rows:
        grouped.setdefault(key_of(row.event), []).append(row)

    series: list[_Series] = []
    for key, members in grouped.items():
        members = sorted(members, key=lambda r: r.event.cash_date)
        months = {(m.event.cash_date.year, m.event.cash_date.month) for m in members}
        count = len(months) if require_distinct_months else len(members)
        if count < min_months:
            continue
        occurrences = [m.event.cash_date for m in members]
        series.append(
            _Series(
                key=str(key),
                day_of_month=day_of(members),
                amounts=tuple(m.home_amount for m in members),
                months_seen=len(months),
                representative_event_id=members[-1].event.event_id,
                description=members[-1].event.description,
                category=members[-1].event.category,
                last_seen=max(occurrences),
                cycle_days=_measure_cycle_days(occurrences, forecast),
            )
        )
    return series


def _select_series(
    history: Sequence[_Normalised],
    request_date: date,
    rule: ProjectionRule,
    forecast: ForecastConfig,
) -> tuple[list[_Series], list[_Normalised]]:
    """Apply the rule's selector. Returns (recurring series, leftover history).

    Leftovers only matter to `hybrid`, which spreads their mean monthly total
    evenly across the window.
    """
    spec = rule.spec

    if spec.family is SelectorFamily.TRAILING:
        cutoff = request_date - timedelta(days=spec.trailing_days)
        chosen = [r for r in history if cutoff <= r.event.cash_date < request_date]
        series = [
            _Series(
                key=r.event.event_id,
                day_of_month=r.event.cash_date.day,
                amounts=(r.home_amount,),
                months_seen=1,
                representative_event_id=r.event.event_id,
                description=r.event.description,
                category=r.event.category,
                last_seen=r.event.cash_date,
                cycle_days=forecast.default_cycle_days,
            )
            for r in chosen
        ]
        return series, []

    if spec.family is SelectorFamily.DAY_STABLE:
        series = _build_series(
            history,
            key_of=lambda e: (e.description, e.cash_date.day),
            day_of=lambda members: members[-1].event.cash_date.day,
            min_months=spec.min_occurrences,
            require_distinct_months=True,
            forecast=forecast,
        )
        return series, []

    if spec.family is SelectorFamily.DESC_ANY:
        series = _build_series(
            history,
            key_of=lambda e: e.description,
            day_of=lambda members: members[-1].event.cash_date.day,
            min_months=spec.min_occurrences,
            require_distinct_months=False,
            forecast=forecast,
        )
        return series, []

    if spec.family is SelectorFamily.CAT_STABLE:
        series = _build_series(
            history,
            key_of=lambda e: (e.category, e.cash_date.day),
            day_of=lambda members: members[-1].event.cash_date.day,
            min_months=spec.min_occurrences,
            require_distinct_months=True,
            forecast=forecast,
        )
        return series, []

    # hybrid: a day_stable_2 core, plus everything else as a flat daily baseline.
    core = _build_series(
        history,
        key_of=lambda e: (e.description, e.cash_date.day),
        day_of=lambda members: members[-1].event.cash_date.day,
        min_months=forecast.hybrid_core_min_months,
        require_distinct_months=True,
        forecast=forecast,
    )
    claimed = {s.key for s in core}
    remainder = [
        r for r in history if str((r.event.description, r.event.cash_date.day)) not in claimed
    ]

    # A recurring commitment the user is allowed to change must survive as a
    # NAMED series. Averaging it into the baseline makes it unreachable: a
    # spending change cites an event_id, and the baseline has none. Keyed on
    # description alone, since a subscription can drift by a day or two.
    flexible = _build_series(
        [r for r in remainder if r.event.flexibility != forecast.fixed_flexibility],
        key_of=lambda e: e.description,
        day_of=lambda members: members[-1].event.cash_date.day,
        min_months=forecast.flexible_series_min_occurrences,
        require_distinct_months=False,
        forecast=forecast,
    )
    flexible_names = {s.key for s in flexible}
    leftovers = [r for r in remainder if r.event.description not in flexible_names]
    return [*core, *flexible], leftovers


def _stable_income_descriptions(
    history: Sequence[_Normalised], forecast: ForecastConfig
) -> frozenset[str]:
    """Descriptions of credits that recur monthly on a fixed day of the month.

    A payroll credit on the 15th of several months qualifies. Gig and platform
    payouts, which land whenever work is invoiced, do not.
    """
    buckets: dict[tuple[str, int], set[tuple[int, int]]] = {}
    for row in history:
        if row.home_amount <= 0:
            continue
        key = (row.event.description, row.event.cash_date.day)
        buckets.setdefault(key, set()).add(
            (row.event.cash_date.year, row.event.cash_date.month)
        )
    return frozenset(
        description
        for (description, _day), months in buckets.items()
        if len(months) >= forecast.hybrid_core_min_months
    )


def _project(
    history: Sequence[_Normalised],
    request_date: date,
    window_end: date,
    rule: ProjectionRule,
    forecast: ForecastConfig,
) -> list[CashFlow]:
    """Emit the projected flows the rule implies, inside the window only."""
    series, leftovers = _select_series(history, request_date, rule, forecast)
    flows: list[CashFlow] = []

    stable_income = (
        _stable_income_descriptions(history, forecast)
        if rule.income_mode is IncomeMode.STABLE_ONLY
        else frozenset()
    )

    for item in series:
        if not is_series_current(item.last_seen, item.cycle_days, request_date, forecast):
            # Already missed a slot: treat the commitment as ended, not ongoing.
            continue

        amount = _pick_amount(item.amounts, rule.amount_mode)

        if amount > 0:
            if rule.income_mode is IncomeMode.NONE:
                continue
            if rule.income_mode is IncomeMode.STABLE_ONLY and item.description not in stable_income:
                continue
        anchor = _on_day_of_month(request_date, item.day_of_month)
        for step in range(rule.horizon_months + 1):
            occurrence = add_months(anchor, step)
            if occurrence < request_date or occurrence > window_end:
                continue
            flows.append(
                CashFlow(
                    on_date=occurrence,
                    amount=amount,
                    source_event_id=item.representative_event_id,
                    reason=InclusionReason.PROJECTED_RECURRING,
                    projected=True,
                    description=item.description,
                    category=item.category,
                )
            )

    # The baseline models IRREGULAR SPENDING, and only that. A credit that did
    # not form a series is a one-off, and spreading a one-off across every day
    # of the window invents recurring income out of nothing -- exactly what
    # AGENTS.md 6.3 forbids.
    #
    # This is not hypothetical. user_03's August 2019 net salary is a single
    # occurrence, so it lands here rather than in a series; averaging it in
    # flipped the whole baseline from -X/day of spending to +5,798/day of
    # income and lifted capacity by 291,000 on one row.
    leftovers = [r for r in leftovers if r.home_amount <= 0]

    if leftovers:
        # Spread the leftover mean MONTHLY total evenly over each day of the
        # window, so irregular spending still applies downward pressure.
        months_of_history = max(
            1,
            len({(r.event.cash_date.year, r.event.cash_date.month) for r in leftovers}),
        )
        monthly_total = sum((r.home_amount for r in leftovers), Decimal(0)) / Decimal(
            months_of_history
        )
        per_day = monthly_total / Decimal(forecast.days_per_month)
        cursor = request_date
        while cursor <= window_end:
            flows.append(
                CashFlow(
                    on_date=cursor,
                    amount=per_day,
                    source_event_id="baseline",
                    reason=InclusionReason.PROJECTED_BASELINE,
                    projected=True,
                    description="irregular spending baseline",
                    category="",
                )
            )
            cursor += timedelta(days=1)

    return flows


# ---------------------------------------------------------------------------
# build_ledger
# ---------------------------------------------------------------------------


def build_ledger(
    user_id: str,
    request_date: date,
    profile: Profile,
    events: Iterable[FinancialEvent],
    rates: RateTable,
    image_amounts: Mapping[str, Decimal],
    rule: ProjectionRule,
    *,
    forecast: ForecastConfig,
    fx: FxConfig,
) -> Ledger:
    """Assemble one user's ledger over the forecast window.

    Opening balance is `profile.current_available_balance` as of `request_date`,
    ALREADY net of settled history; settled events are never replayed against
    it. They inform the projection only.
    """
    window_end = request_date + timedelta(days=forecast.horizon_days)
    mine = [e for e in events if e.user_id == user_id]

    excluded: list[ExcludedEvent] = []
    missing: list[str] = []
    fallback_notes: list[str] = []
    fx_errors: list[str] = []

    chained, chain_exclusions = collapse_chains(mine)
    excluded.extend(chain_exclusions)

    # NOTE: internal-transfer exclusion is deliberately NOT applied. The
    # hypothesis was falsified -- see CLAUDE.md D1 and
    # tests/test_ledger.py::test_dataset_contains_no_internal_transfer_pairs.
    # `detect_internal_transfers` is retained only as a data-invariant probe.

    # -- resolve amounts and currency, once, up front -----------------------
    normalised: list[_Normalised] = []
    for event in mine:
        if event.event_id in chained:
            continue

        reason = _status_exclusion(event, forecast)
        if reason is not None:
            excluded.append(ExcludedEvent(event_id=event.event_id, reason=reason))
            continue

        amount = event.amount
        if amount is None:
            override = image_amounts.get(event.event_id)
            if override is None:
                # A blank amount is NEVER zero. Record it and drop it rather
                # than guess a number that would silently skew the forecast.
                missing.append(event.event_id)
                excluded.append(
                    ExcludedEvent(
                        event_id=event.event_id,
                        reason=ExclusionReason.BLANK_AMOUNT,
                        detail="no image-derived amount supplied",
                    )
                )
                continue
            amount = to_money(override)

        try:
            row = _normalise(event, amount, profile.home_currency, rates, fx)
        except FXError as exc:
            fx_errors.append(f"{event.event_id}: {exc}")
            excluded.append(
                ExcludedEvent(
                    event_id=event.event_id,
                    reason=ExclusionReason.STATUS_EXCLUDED,
                    detail=f"fx failure: {exc}",
                )
            )
            continue

        for fallback in row.fallbacks:
            fallback_notes.append(fallback.describe(event.event_id))
        normalised.append(row)

    # -- explicit future flows ----------------------------------------------
    flows: list[CashFlow] = []
    history: list[_Normalised] = []

    for row in normalised:
        event = row.event
        signed = -row.home_amount if event.direction is Direction.DEBIT else row.home_amount

        if event.status is EventStatus.PENDING:
            # Pending DEBITS land on settlement_date, never before the request.
            landing = max(event.cash_date, request_date)
            if landing > window_end:
                excluded.append(
                    ExcludedEvent(event_id=event.event_id, reason=ExclusionReason.OUTSIDE_WINDOW)
                )
                continue
            flows.append(
                CashFlow(
                    on_date=landing,
                    amount=signed,
                    source_event_id=event.event_id,
                    reason=InclusionReason.PENDING_DEBIT,
                    projected=False,
                    description=event.description,
                    category=event.category,
                    original_amount=event.amount,
                    original_currency=event.currency,
                )
            )
            continue

        if event.status is EventStatus.SCHEDULED:
            if event.event_date < request_date:
                excluded.append(
                    ExcludedEvent(
                        event_id=event.event_id,
                        reason=ExclusionReason.SETTLED_HISTORY,
                        detail="scheduled but dated before the request",
                    )
                )
                continue
            if event.event_date > window_end:
                excluded.append(
                    ExcludedEvent(event_id=event.event_id, reason=ExclusionReason.OUTSIDE_WINDOW)
                )
                continue
            flows.append(
                CashFlow(
                    on_date=event.event_date,
                    amount=signed,
                    source_event_id=event.event_id,
                    reason=InclusionReason.SCHEDULED_FUTURE,
                    projected=False,
                    description=event.description,
                    category=event.category,
                    original_amount=event.amount,
                    original_currency=event.currency,
                )
            )
            continue

        # Settled. Already inside the opening balance -- history only.
        if event.cash_date < request_date:
            history.append(row)
        else:
            excluded.append(
                ExcludedEvent(
                    event_id=event.event_id,
                    reason=ExclusionReason.SETTLED_HISTORY,
                    detail="settled on or after the request date",
                )
            )

    # -- projected recurrence ------------------------------------------------
    signed_history = [
        _Normalised(
            event=row.event,
            home_amount=(
                -row.home_amount if row.event.direction is Direction.DEBIT else row.home_amount
            ),
            fallbacks=row.fallbacks,
        )
        for row in history
    ]
    flows.extend(_project(signed_history, request_date, window_end, rule, forecast))

    # A projected occurrence can collide with the real future event it was
    # inferred from. Matching on description or event_id is not enough: a
    # scheduled salary is filed as "Next confirmed salary" under a fresh id,
    # while the history it was inferred from says "Primary household salary".
    # Same date, same category, same direction is the same money, and the
    # explicit row is the truth -- so the projection gives way.
    explicit_slots = {
        (f.on_date, f.category, f.amount >= 0)
        for f in flows
        if f.reason in (InclusionReason.SCHEDULED_FUTURE, InclusionReason.PENDING_DEBIT)
    }
    deduped = [
        f
        for f in flows
        if not (
            f.reason is InclusionReason.PROJECTED_RECURRING
            and (f.on_date, f.category, f.amount >= 0) in explicit_slots
        )
    ]

    if not rule.include_request_date:
        deduped = [f for f in deduped if f.on_date != request_date]

    deduped.sort(key=lambda f: (f.on_date, f.source_event_id))

    return Ledger(
        user_id=user_id,
        request_date=request_date,
        window_end=window_end,
        home_currency=profile.home_currency,
        opening_balance=profile.current_available_balance,
        flows=tuple(deduped),
        missing_amounts=tuple(missing),
        excluded=tuple(excluded),
        fx_fallbacks=tuple(fallback_notes),
        fx_errors=tuple(fx_errors),
    )
