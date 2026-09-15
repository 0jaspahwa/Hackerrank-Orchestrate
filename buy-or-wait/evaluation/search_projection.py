"""Sweep `ProjectionRule` against the 25 labelled rows.

Recurrence is not supplied by the dataset, so the projection rule is the
dominant driver of every capacity number. This sweeps the rule space and reports
which rules reproduce the labels.

OVERFITTING GUARD -- read the output with this in mind. n=25, so one row is 4
percentage points, and a sweep over ~100 rule combinations will find something
that fits by chance. The report therefore prints the runner-up rules next to the
winner, flags structurally different rules that score within a row or two, and
names the rows the best rule still gets wrong. Prefer the simplest rule among
near-ties; a rule that wins by one row has not won.

Usage:
    python -m evaluation.search_projection
    python -m evaluation.search_projection --top 8
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.score import LabelledRequest, load_labelled_requests  # noqa: E402
from src.capacity import (  # noqa: E402
    amount_safe_to_pay,
    earliest_date_for_full_payment,
)
from src.config import Config, default_config  # noqa: E402
from src.contract import parse_money  # noqa: E402
from src.fx import RateTable, load_rates  # noqa: E402
from src.ledger import (  # noqa: E402
    AmountMode,
    IncomeMode,
    FinancialEvent,
    Profile,
    ProjectionRule,
    build_ledger,
    load_events,
    load_profiles,
)

#: The selector names swept. Exactly the grid in the brief.
SELECTORS: tuple[str, ...] = (
    "trailing_25",
    "trailing_30",
    "trailing_31",
    "trailing_35",
    "day_stable_2",
    "day_stable_3",
    "desc_any_3",
    "cat_stable_2",
    "hybrid",
)

HORIZONS: tuple[int, ...] = (3, 4)
INCLUDE_T: tuple[bool, ...] = (True, False)

#: How much future income the rule may invent. AGENTS.md 6.3 forbids inventing
#: unsupported income, and the labels are not symmetric about it, so this is a
#: swept axis rather than a fixed choice.
INCOME_MODES: tuple[IncomeMode, ...] = tuple(IncomeMode)

#: Recency-guard settings swept. None disables the guard entirely.
RECENCY_CYCLES: tuple[float | None, ...] = (1.25, 1.5, 2.0, None)

#: A prediction counts as exact within this absolute tolerance.
EXACT_TOLERANCE = Decimal("0.01")

NEAR_TOLERANCES: tuple[tuple[str, Decimal], ...] = (
    ("within_1pct", Decimal("0.01")),
    ("within_5pct", Decimal("0.05")),
)

#: Rules scoring within this many rows of the best are treated as tied.
TIE_ROWS = 2


@dataclass(frozen=True)
class RowResult:
    """One rule's prediction for one labelled row."""

    request_id: str
    expected_amount: Decimal
    actual_amount: Decimal
    requested_amount: Decimal
    expected_earliest: date | None
    actual_earliest: date | None

    @property
    def absolute_error(self) -> Decimal:
        return abs(self.expected_amount - self.actual_amount)

    @property
    def relative_error(self) -> Decimal:
        """Error relative to the quantity being PREDICTED, not to the request.

        Dividing by `requested_amount` flatters the result badly: predicting
        59,208 where the label says 1,425,000 is a 96% miss, but against a
        60,496,000 request it reads as 2.3%. The denominator is the expected
        amount_safe_to_pay, which is what the projection rule is actually
        trying to reproduce.
        """
        if self.expected_amount == 0:
            return Decimal(0) if self.absolute_error <= EXACT_TOLERANCE else Decimal(1)
        return self.absolute_error / abs(self.expected_amount)

    @property
    def share_of_request(self) -> Decimal:
        """The same error expressed against the request. Reported, never ranked."""
        base = self.requested_amount if self.requested_amount else Decimal(1)
        return self.absolute_error / abs(base)

    @property
    def is_exact(self) -> bool:
        return self.absolute_error <= EXACT_TOLERANCE

    @property
    def earliest_matches(self) -> bool:
        return self.expected_earliest == self.actual_earliest

    @property
    def capacity_too_high(self) -> bool:
        """Predicted more headroom than the label: we under-projected expense."""
        return self.actual_amount - self.expected_amount > EXACT_TOLERANCE

    @property
    def capacity_too_low(self) -> bool:
        return self.expected_amount - self.actual_amount > EXACT_TOLERANCE


@dataclass(frozen=True)
class RuleScore:
    """Aggregate performance of one rule over the labelled rows."""

    rule: ProjectionRule
    rows: tuple[RowResult, ...]
    recency_cycles: float | None = None

    @property
    def label(self) -> str:
        guard = "off" if self.recency_cycles is None else f"{self.recency_cycles:g}"
        return f"{self.rule.label()}/rec:{guard}"

    @property
    def exact(self) -> int:
        return sum(1 for r in self.rows if r.is_exact)

    @property
    def within_1pct(self) -> int:
        return sum(1 for r in self.rows if r.relative_error <= Decimal("0.01"))

    @property
    def within_5pct(self) -> int:
        return sum(1 for r in self.rows if r.relative_error <= Decimal("0.05"))

    @property
    def earliest_exact(self) -> int:
        return sum(1 for r in self.rows if r.earliest_matches)

    @property
    def median_relative_error(self) -> Decimal:
        if not self.rows:
            return Decimal(0)
        return Decimal(str(median(sorted(float(r.relative_error) for r in self.rows))))

    @property
    def too_high(self) -> int:
        return sum(1 for r in self.rows if r.capacity_too_high)

    @property
    def too_low(self) -> int:
        return sum(1 for r in self.rows if r.capacity_too_low)

    @property
    def sort_key(self) -> tuple:
        """Most rows near the label first; median error settles ties."""
        return (
            -self.within_5pct,
            -self.within_1pct,
            -self.earliest_exact,
            float(self.median_relative_error),
        )


def evaluate_rule(
    rule: ProjectionRule,
    requests: Sequence[LabelledRequest],
    events_by_user: dict[str, tuple[FinancialEvent, ...]],
    profiles: dict[str, Profile],
    rates: RateTable,
    config: Config,
) -> RuleScore:
    """Run one rule over every labelled row. No I/O; pure over its inputs."""
    rows: list[RowResult] = []
    for request in requests:
        profile = profiles[request.user_id]
        ledger = build_ledger(
            request.user_id,
            request.request_date,
            profile,
            events_by_user.get(request.user_id, ()),
            rates,
            {},
            rule,
            forecast=config.forecast,
            fx=config.fx,
        )
        actual = amount_safe_to_pay(
            ledger, profile.minimum_balance_to_keep, request.requested_amount
        )
        earliest = earliest_date_for_full_payment(
            ledger,
            profile.minimum_balance_to_keep,
            request.requested_amount,
            ledger.window_end,
        )
        expected_earliest_raw = request.expected["earliest_date_for_full_payment"]
        rows.append(
            RowResult(
                request_id=request.request_id,
                expected_amount=parse_money(request.expected["amount_safe_to_pay"]),
                actual_amount=actual,
                requested_amount=request.requested_amount,
                expected_earliest=(
                    date.fromisoformat(expected_earliest_raw) if expected_earliest_raw else None
                ),
                actual_earliest=earliest,
            )
        )
    return RuleScore(rule=rule, rows=tuple(rows), recency_cycles=config.forecast.recency_cycles)


def build_grid() -> tuple[ProjectionRule, ...]:
    """The full cross product from the brief."""
    return tuple(
        ProjectionRule(
            selector=selector,
            amount_mode=mode,
            horizon_months=horizon,
            include_request_date=include_t,
            income_mode=income,
        )
        for selector in SELECTORS
        for mode in AmountMode
        for horizon in HORIZONS
        for include_t in INCLUDE_T
        for income in INCOME_MODES
    )


def run_search(config: Config, top: int = 5) -> list[RuleScore]:
    """Evaluate every rule and print the report."""
    requests = load_labelled_requests(config)
    rates = load_rates(config.paths.exchange_rates_csv)
    profiles = load_profiles(config.paths.financial_profiles_csv)

    wanted = {r.user_id for r in requests}
    events_by_user: dict[str, list[FinancialEvent]] = {u: [] for u in wanted}
    for event in load_events(config.paths.financial_events_csv):
        if event.user_id in wanted:
            events_by_user[event.user_id].append(event)
    indexed = {user: tuple(rows) for user, rows in events_by_user.items()}

    grid = build_grid()
    print(
        f"Evaluating {len(grid)} rules x {len(RECENCY_CYCLES)} recency settings "
        f"over {len(requests)} labelled rows ...",
        file=sys.stderr,
    )

    scores = []
    for cycles in RECENCY_CYCLES:
        scoped = config.model_copy(
            update={"forecast": config.forecast.model_copy(update={"recency_cycles": cycles})}
        )
        for rule in grid:
            scores.append(evaluate_rule(rule, requests, indexed, profiles, rates, scoped))
    scores.sort(key=lambda s: s.sort_key)

    _print_report(scores, requests, top=top)
    return scores


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(
    scores: Sequence[RuleScore], requests: Sequence[LabelledRequest], *, top: int
) -> None:
    n = len(requests)
    pp = 100.0 / n if n else 0.0

    print()
    print("=" * 104)
    print("  PROJECTION RULE SEARCH")
    print("=" * 104)
    print(f"  {len(scores)} rule/recency combinations x {n} labelled rows.")
    print(f"  n={n}: one row is {pp:.1f} percentage points. Read every gap below in ROWS, not %.")
    print()

    print("-" * 104)
    print(f"  TOP {top} RULES")
    print("-" * 104)
    header = (
        f"  {'#':<3}{'rule':<62}{'<=5%':>6}{'<=1%':>6}{'exact':>7}"
        f"{'date':>6}{'medRelErr':>11}{'hi/lo':>9}"
    )
    print(header)
    for index, score in enumerate(scores[:top], start=1):
        print(
            f"  {index:<3}{score.label:<62}{score.within_5pct:>6}{score.within_1pct:>6}"
            f"{score.exact:>7}{score.earliest_exact:>6}"
            f"{float(score.median_relative_error) * 100:>10.1f}%"
            f"{score.too_high:>5}/{score.too_low:<4}"
        )
    print()
    print("  <=5%/<=1%/exact : rows whose amount_safe_to_pay is that close to the label,")
    print("                    measured against the LABEL AMOUNT, not against requested_amount.")
    print("  date            : rows whose earliest_date_for_full_payment matches exactly")
    print("  hi/lo           : rows where predicted capacity is too HIGH / too LOW.")
    print("                    too HIGH means we projected too little expense, and vice versa.")

    _print_tie_warning(scores, top=top)

    best = scores[0]
    _print_winner_rows(best, requests)


def _family(rule: ProjectionRule) -> str:
    return rule.spec.family.value


def _print_tie_warning(scores: Sequence[RuleScore], *, top: int) -> None:
    best = scores[0]
    tied = [s for s in scores if best.within_5pct - s.within_5pct <= TIE_ROWS]
    families = sorted({_family(s.rule) for s in tied})

    print()
    print("-" * 104)
    print("  OVERFITTING CHECK")
    print("-" * 104)
    print(
        f"  {len(tied)} of {len(scores)} rules land within {TIE_ROWS} rows of the best "
        f"({best.within_5pct}/{len(best.rows)} at <=5%)."
    )
    print(f"  Selector families represented in that tie band: {', '.join(families)}")
    if len(families) > 1:
        print()
        print("  >> Structurally DIFFERENT rules are tied. At n=25 that is not a winner,")
        print("     it is a plateau. Prefer the simplest rule in the band, and treat the")
        print("     ranking above as a shortlist rather than a result.")
    else:
        print()
        print(f"  >> All tied rules come from the '{families[0]}' family, which is weak")
        print("     evidence that the family is real. The exact parameter still is not")
        print("     settled by 25 rows.")

    simplest = min(
        tied,
        key=lambda s: (
            _family(s.rule) != "trailing",
            s.rule.income_mode is not IncomeMode.ALL,
            s.rule.horizon_months,
            s.rule.amount_mode is not AmountMode.LAST,
            s.rule.label(),
        ),
    )
    _print_recency_effect(scores)

    print()
    print(f"  Simplest rule inside the tie band: {simplest.label}")
    print(
        f"     scores {simplest.within_5pct} <=5%, {simplest.within_1pct} <=1%, "
        f"{simplest.earliest_exact} dates  "
        f"(best is {best.within_5pct}/{best.within_1pct}/{best.earliest_exact})"
    )


def _print_recency_effect(scores: Sequence[RuleScore]) -> None:
    """Best achievable score at each recency setting, holding nothing else fixed."""
    print()
    print("  RECENCY GUARD -- best rule at each setting:")
    print(f"     {'recency':<10}{'<=5%':>6}{'<=1%':>6}{'date':>6}  best rule")
    for cycles in RECENCY_CYCLES:
        band = [s for s in scores if s.recency_cycles == cycles]
        if not band:
            continue
        best = min(band, key=lambda s: s.sort_key)
        guard = "off" if cycles is None else f"{cycles:g}"
        print(
            f"     {guard:<10}{best.within_5pct:>6}{best.within_1pct:>6}"
            f"{best.earliest_exact:>6}  {best.rule.label()}"
        )

    print()
    print("  Best score per selector family (any recency setting):")
    families = sorted({_family(s.rule) for s in scores})
    for family in families:
        band = [s for s in scores if _family(s.rule) == family]
        best = min(band, key=lambda s: s.sort_key)
        print(f"     {family:<12}{best.within_5pct:>3} <=5%  {best.earliest_exact:>3} dates   {best.label}")


def _print_winner_rows(score: RuleScore, requests: Sequence[LabelledRequest]) -> None:
    by_id = {r.request_id: r for r in requests}
    print()
    print("-" * 104)
    print(f"  PER-ROW DETAIL FOR THE TOP RULE: {score.label}")
    print("-" * 104)
    print(
        f"  {'request':<12}{'expected':>16}{'actual':>16}{'relErr':>9}{'/req':>8}"
        f"  {'exp date':<12}{'act date':<12}  verdict"
    )

    for row in score.rows:
        request = by_id[row.request_id]
        verdict = (
            "ok"
            if row.relative_error <= Decimal("0.05")
            else ("capacity HIGH" if row.capacity_too_high else "capacity LOW")
        )
        date_mark = "" if row.earliest_matches else "  date X"
        print(
            f"  {row.request_id:<12}{float(row.expected_amount):>16,.2f}"
            f"{float(row.actual_amount):>16,.2f}"
            f"{float(row.relative_error) * 100:>8.1f}%"
            f"{float(row.share_of_request) * 100:>7.1f}%"
            f"  {str(row.expected_earliest or '-'):<12}{str(row.actual_earliest or '-'):<12}"
            f"  {verdict}{date_mark}"
        )
        _ = request

    misses = [r for r in score.rows if r.relative_error > Decimal("0.05")]
    print()
    print(f"  Rows still wrong at >5%: {len(misses)} of {len(score.rows)}")
    if misses:
        print(f"     {', '.join(r.request_id for r in misses)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=5, help="how many rules to list")
    args = parser.parse_args(argv)
    run_search(default_config(), top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
