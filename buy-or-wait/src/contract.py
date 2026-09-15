"""The single source of truth for the `output.csv` contract.

Everything the solution emits passes through this module. The enums, the two
money formatters, the plan/change serialisers and the cross-field invariants are
defined here ONCE; every other module imports them and defines none of its own.

Nothing here does I/O, touches the clock, or needs an API key.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Iterable, Self, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Column order. This tuple is the authority; the CSV writer and the scorer both
# read it rather than restating the header.
# ---------------------------------------------------------------------------

OUTPUT_COLUMNS: tuple[str, ...] = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

#: The literal emitted for an empty payment plan and for an empty change list.
NONE_LITERAL = "none"

#: Separator between payment-plan entries and between spending-change actions.
LIST_SEPARATOR = "|"

#: Separator inside a single entry ("<date>:<amount>", "reduce_to:<id>:<amt>").
FIELD_SEPARATOR = ":"

#: AGENTS.md 6.2 -- at most three spending-change actions.
MAX_SPENDING_CHANGES = 3

#: Money is compared at this tolerance; below it two amounts are the same money.
MONEY_EPSILON = Decimal("0.005")

ISO_DATE = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Enums -- defined once, imported everywhere.
# ---------------------------------------------------------------------------


class AffordabilityStatus(StrEnum):
    """The `affordability_status` column. Closed set, AGENTS.md 6.2."""

    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class PaymentMethod(StrEnum):
    """The `recommended_payment_method` column. Closed set, AGENTS.md 6.2."""

    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class ChangeAction(StrEnum):
    """The two permitted spending-change verbs."""

    STOP = "stop"
    REDUCE_TO = "reduce_to"


# ---------------------------------------------------------------------------
# Money formatters. The ONLY place money becomes a string.
#
# Two different conventions live in the same output row. They are not a mistake
# and must not be unified -- see CLAUDE.md, Addendum A.
# ---------------------------------------------------------------------------


def to_money(value: Decimal | float | int | str) -> Decimal:
    """Coerce to Decimal without ever routing a float through binary noise."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"not a money value: {value!r}") from exc


def quantize_down(value: Decimal | float | int | str, places: int = 2) -> Decimal:
    """Round DOWN to `places`.

    `amount_safe_to_pay` is a maximum-safe quantity: rounding it up by even one
    minor unit can breach `minimum_balance_to_keep`, so it always rounds down.
    """
    if places < 0:
        raise ValueError("places must be >= 0")
    exp = Decimal(1).scaleb(-places)
    return to_money(value).quantize(exp, rounding=ROUND_DOWN)


def quantize_half_up(value: Decimal | float | int | str, places: int = 2) -> Decimal:
    """Round half-up to `places`. For scheduled amounts, which are exact."""
    exp = Decimal(1).scaleb(-places)
    return to_money(value).quantize(exp, rounding=ROUND_HALF_UP)


def format_safe_amount(value: Decimal | float | int | str) -> str:
    """Format `amount_safe_to_pay`: minimal form, trailing zeros stripped.

    Matches the labels: ``462``, ``603.3``, ``17229139.2``.
    """
    d = to_money(value).normalize()
    if d == 0:
        return "0"
    sign, _, exponent = d.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        # normalize() turns 4620 into 4.62E+3; expand it back out.
        d = d.quantize(Decimal(1))
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_plan_amount(value: Decimal | float | int | str) -> str:
    """Format a `payment_plan` / `reduce_to` amount.

    Two decimals when a fractional part exists, plain integer otherwise --
    ``25256``, ``620.40``, ``15952906.67``. See CLAUDE.md Addendum A: the labels
    contain no ``25256.00``, so an unconditional ``.2f`` is wrong.
    """
    d = quantize_half_up(value, 2)
    if d == d.to_integral_value():
        return format(d.to_integral_value(), "f")
    return format(d, "f")


def parse_money(text: str) -> Decimal:
    """Inverse of either formatter. Used by the scorer and by round-trip tests."""
    return to_money(text.strip())


# ---------------------------------------------------------------------------
# Payment plan.
# ---------------------------------------------------------------------------


class PaymentEntry(BaseModel):
    """One dated payment inside a plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    due_date: date
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce(cls, v: object) -> Decimal:
        return to_money(v)  # type: ignore[arg-type]

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"payment amount must be > 0, got {v}")
        return v

    def serialise(self) -> str:
        return f"{self.due_date:{ISO_DATE}}{FIELD_SEPARATOR}{format_plan_amount(self.amount)}"

    @classmethod
    def parse(cls, text: str) -> "PaymentEntry":
        raw_date, _, raw_amount = text.strip().partition(FIELD_SEPARATOR)
        if not raw_amount:
            raise ValueError(f"malformed payment entry: {text!r}")
        return cls(due_date=date.fromisoformat(raw_date.strip()), amount=parse_money(raw_amount))


class PaymentPlan(BaseModel):
    """A chronological list of payments, or empty (serialising to ``none``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[PaymentEntry, ...] = ()

    @model_validator(mode="after")
    def _chronological(self) -> Self:
        dates = [e.due_date for e in self.entries]
        if dates != sorted(dates):
            raise ValueError(f"payment plan must be chronological, got {dates}")
        return self

    @classmethod
    def empty(cls) -> "PaymentPlan":
        return cls(entries=())

    @classmethod
    def of(cls, pairs: Iterable[tuple[date, Decimal | float | int | str]]) -> "PaymentPlan":
        return cls(entries=tuple(PaymentEntry(due_date=d, amount=to_money(a)) for d, a in pairs))

    @property
    def total(self) -> Decimal:
        return sum((e.amount for e in self.entries), Decimal(0))

    @property
    def start_date(self) -> date | None:
        return self.entries[0].due_date if self.entries else None

    def __len__(self) -> int:
        return len(self.entries)

    def serialise(self) -> str:
        if not self.entries:
            return NONE_LITERAL
        return LIST_SEPARATOR.join(e.serialise() for e in self.entries)

    @classmethod
    def parse(cls, text: str) -> "PaymentPlan":
        cleaned = (text or "").strip()
        if not cleaned or cleaned == NONE_LITERAL:
            return cls.empty()
        return cls(entries=tuple(PaymentEntry.parse(p) for p in cleaned.split(LIST_SEPARATOR)))


# ---------------------------------------------------------------------------
# Spending changes.
# ---------------------------------------------------------------------------


class SpendingChange(BaseModel):
    """``stop:<event_id>`` or ``reduce_to:<event_id>:<new_amount>``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ChangeAction
    event_id: str = Field(min_length=1)
    new_amount: Decimal | None = None

    @field_validator("new_amount", mode="before")
    @classmethod
    def _coerce(cls, v: object) -> Decimal | None:
        return None if v is None else to_money(v)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _amount_matches_action(self) -> Self:
        if self.action is ChangeAction.STOP and self.new_amount is not None:
            raise ValueError("stop must not carry a new_amount")
        if self.action is ChangeAction.REDUCE_TO:
            if self.new_amount is None:
                raise ValueError("reduce_to requires a new_amount")
            if self.new_amount < 0:
                raise ValueError(f"reduce_to amount must be >= 0, got {self.new_amount}")
        return self

    @classmethod
    def stop(cls, event_id: str) -> "SpendingChange":
        return cls(action=ChangeAction.STOP, event_id=event_id)

    @classmethod
    def reduce_to(cls, event_id: str, new_amount: Decimal | float | int | str) -> "SpendingChange":
        return cls(action=ChangeAction.REDUCE_TO, event_id=event_id, new_amount=to_money(new_amount))

    def serialise(self) -> str:
        if self.action is ChangeAction.STOP:
            return f"{ChangeAction.STOP.value}{FIELD_SEPARATOR}{self.event_id}"
        assert self.new_amount is not None  # guaranteed by _amount_matches_action
        return (
            f"{ChangeAction.REDUCE_TO.value}{FIELD_SEPARATOR}{self.event_id}"
            f"{FIELD_SEPARATOR}{format_plan_amount(self.new_amount)}"
        )

    @classmethod
    def parse(cls, text: str) -> "SpendingChange":
        parts = text.strip().split(FIELD_SEPARATOR)
        if parts[0] == ChangeAction.STOP.value and len(parts) == 2:
            return cls.stop(parts[1])
        if parts[0] == ChangeAction.REDUCE_TO.value and len(parts) == 3:
            return cls.reduce_to(parts[1], parse_money(parts[2]))
        raise ValueError(f"malformed spending change: {text!r}")


class SpendingChangeSet(BaseModel):
    """Up to three changes; no event may be both stopped and reduced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changes: tuple[SpendingChange, ...] = ()

    @classmethod
    def empty(cls) -> "SpendingChangeSet":
        return cls(changes=())

    def __len__(self) -> int:
        return len(self.changes)

    def serialise(self) -> str:
        if not self.changes:
            return NONE_LITERAL
        return LIST_SEPARATOR.join(c.serialise() for c in self.changes)

    @classmethod
    def parse(cls, text: str) -> "SpendingChangeSet":
        cleaned = (text or "").strip()
        if not cleaned or cleaned == NONE_LITERAL:
            return cls.empty()
        return cls(changes=tuple(SpendingChange.parse(p) for p in cleaned.split(LIST_SEPARATOR)))


# ---------------------------------------------------------------------------
# The output row.
# ---------------------------------------------------------------------------


class OutputRow(BaseModel):
    """One row of `output.csv`, plus the request facts its invariants need.

    `requested_amount` and `request_date` are carried for validation only; they
    are not emitted. `to_csv_dict()` emits exactly `OUTPUT_COLUMNS`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: PaymentPlan
    earliest_date_for_full_payment: date | None
    spending_changes_needed: SpendingChangeSet
    decision_explanation: str

    # Context for validate(); never written to the CSV.
    request_date: date | None = None
    requested_amount: Decimal | None = None

    @field_validator("amount_safe_to_pay", "requested_amount", mode="before")
    @classmethod
    def _coerce(cls, v: object) -> Decimal | None:
        return None if v is None else to_money(v)  # type: ignore[arg-type]

    # -- invariants ---------------------------------------------------------

    def validate_contract(self) -> Self:
        """Enforce every cross-field invariant. Violations raise `ContractError`.

        Called by `to_csv_dict()`, so nothing reaches the CSV unvalidated.
        """
        asp = self.amount_safe_to_pay
        status = self.affordability_status
        method = self.recommended_payment_method
        plan = self.payment_plan

        if asp < 0:
            raise ContractError(f"{self.request_id}: amount_safe_to_pay {asp} < 0")
        if self.requested_amount is not None and asp > self.requested_amount + MONEY_EPSILON:
            raise ContractError(
                f"{self.request_id}: amount_safe_to_pay {asp} > requested_amount "
                f"{self.requested_amount}"
            )

        if status is AffordabilityStatus.AFFORDABLE_NOW:
            if self.earliest_date_for_full_payment is None:
                raise ContractError(
                    f"{self.request_id}: affordable_now requires "
                    f"earliest_date_for_full_payment == request_date, got empty"
                )
            if (
                self.request_date is not None
                and self.earliest_date_for_full_payment != self.request_date
            ):
                raise ContractError(
                    f"{self.request_id}: affordable_now requires earliest "
                    f"{self.earliest_date_for_full_payment} == request_date {self.request_date}"
                )

        if method is PaymentMethod.PARTIAL_PAYMENT:
            if status is not AffordabilityStatus.AFFORDABLE_WITH_PLAN:
                raise ContractError(
                    f"{self.request_id}: partial_payment requires affordable_with_plan, "
                    f"got {status.value}"
                )
            if len(plan) != 2:
                raise ContractError(
                    f"{self.request_id}: partial_payment requires exactly 2 payments, "
                    f"got {len(plan)}"
                )
            if self.requested_amount is not None:
                gap = abs(plan.total - self.requested_amount)
                if gap > MONEY_EPSILON:
                    raise ContractError(
                        f"{self.request_id}: partial_payment plan totals {plan.total}, "
                        f"expected requested_amount {self.requested_amount}"
                    )

        if method is PaymentMethod.NOT_RECOMMENDED and len(plan) != 0:
            raise ContractError(
                f"{self.request_id}: not_recommended requires payment_plan 'none', "
                f"got {plan.serialise()!r}"
            )

        stopped = {c.event_id for c in self.spending_changes_needed.changes if c.action is ChangeAction.STOP}
        reduced = {
            c.event_id for c in self.spending_changes_needed.changes if c.action is ChangeAction.REDUCE_TO
        }
        both = stopped & reduced
        if both:
            raise ContractError(
                f"{self.request_id}: event(s) both stopped and reduced: {sorted(both)}"
            )

        if len(self.spending_changes_needed) > MAX_SPENDING_CHANGES:
            raise ContractError(
                f"{self.request_id}: {len(self.spending_changes_needed)} spending changes "
                f"exceeds the maximum of {MAX_SPENDING_CHANGES}"
            )

        return self

    #: Alias so callers can write `row.validate()` as the task spec phrases it.
    #: `BaseModel.validate` is deprecated-and-removed in pydantic v2, so this
    #: name is free.
    validate = validate_contract  # type: ignore[assignment]

    # -- serialisation ------------------------------------------------------

    def to_csv_dict(self) -> dict[str, str]:
        """Validate, then render the eight columns as strings."""
        self.validate_contract()
        earliest = self.earliest_date_for_full_payment
        return {
            "request_id": self.request_id,
            "amount_safe_to_pay": format_safe_amount(self.amount_safe_to_pay),
            "affordability_status": self.affordability_status.value,
            "recommended_payment_method": self.recommended_payment_method.value,
            "payment_plan": self.payment_plan.serialise(),
            "earliest_date_for_full_payment": f"{earliest:{ISO_DATE}}" if earliest else "",
            "spending_changes_needed": self.spending_changes_needed.serialise(),
            "decision_explanation": self.decision_explanation,
        }

    def to_csv_line(self) -> str:
        """Render as a single CSV line (no header), quoted per `csv` defaults."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(OUTPUT_COLUMNS), lineterminator="")
        writer.writerow(self.to_csv_dict())
        return buffer.getvalue()

    @classmethod
    def from_csv_dict(
        cls,
        row: dict[str, str],
        *,
        request_date: date | None = None,
        requested_amount: Decimal | float | int | str | None = None,
    ) -> "OutputRow":
        """Parse a CSV row back into an `OutputRow`. Inverse of `to_csv_dict()`."""
        earliest_raw = (row.get("earliest_date_for_full_payment") or "").strip()
        return cls(
            request_id=row["request_id"],
            amount_safe_to_pay=parse_money(row["amount_safe_to_pay"]),
            affordability_status=AffordabilityStatus(row["affordability_status"].strip()),
            recommended_payment_method=PaymentMethod(row["recommended_payment_method"].strip()),
            payment_plan=PaymentPlan.parse(row.get("payment_plan", "")),
            earliest_date_for_full_payment=date.fromisoformat(earliest_raw) if earliest_raw else None,
            spending_changes_needed=SpendingChangeSet.parse(row.get("spending_changes_needed", "")),
            decision_explanation=row.get("decision_explanation", ""),
            request_date=request_date,
            requested_amount=None if requested_amount is None else to_money(requested_amount),
        )


class ContractError(ValueError):
    """A cross-field invariant of `OutputRow` was violated."""


# ---------------------------------------------------------------------------
# Safe default.
# ---------------------------------------------------------------------------

SAFE_DEFAULT_EXPLANATION = (
    "No recommendation was made because the available records did not provide "
    "enough verified information to confirm this payment is safe."
)


def safe_default_row(
    request_id: str,
    *,
    request_date: date | None = None,
    requested_amount: Decimal | float | int | str | None = None,
    explanation: str = SAFE_DEFAULT_EXPLANATION,
) -> OutputRow:
    """The row emitted when the pipeline cannot justify anything else.

    Zero safe, not affordable, not recommended, no plan, no date, no changes.
    Never raises, so it is always available as a fallback.
    """
    return OutputRow(
        request_id=request_id,
        amount_safe_to_pay=Decimal(0),
        affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
        recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
        payment_plan=PaymentPlan.empty(),
        earliest_date_for_full_payment=None,
        spending_changes_needed=SpendingChangeSet.empty(),
        decision_explanation=explanation,
        request_date=request_date,
        requested_amount=None if requested_amount is None else to_money(requested_amount),
    )


def write_output_csv(path: str, rows: Sequence[OutputRow]) -> None:
    """Write `output.csv` with exactly `OUTPUT_COLUMNS`, in order.

    The only I/O in this module, kept here so the header is never restated.
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())
