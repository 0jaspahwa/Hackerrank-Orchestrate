"""Per-field accuracy over all seven output columns, against the 25 labels.

n=25, so every single row is 4 percentage points -- that caveat is printed next
to every aggregate number, because at this sample size a 4-point move is one row
changing its mind, not a trend.

`decision_explanation` is NOT scored. There is no honest automated metric for it
at n=25, so expected and actual are printed side by side for a human to read.

Usage:
    python -m evaluation.score              # the real pipeline
    python -m evaluation.score --stub       # SAFE_DEFAULT for every row
    python -m evaluation.score --quiet      # aggregates and matrices only
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, default_config  # noqa: E402
from src.contract import (  # noqa: E402
    MONEY_EPSILON,
    OutputRow,
    format_safe_amount,
    parse_money,
    safe_default_row,
)

NUMERIC_FIELD = "amount_safe_to_pay"

#: Scored by exact match, and given a confusion matrix.
CATEGORICAL_FIELDS: tuple[str, ...] = (
    "affordability_status",
    "recommended_payment_method",
)

#: Scored by exact string match. A miss here is a structural bug, not a judgement.
STRUCTURAL_FIELDS: tuple[str, ...] = (
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)

UNSCORED_FIELD = "decision_explanation"

NEAR_TOLERANCES: tuple[tuple[str, Decimal], ...] = (
    ("<=1%", Decimal("0.01")),
    ("<=5%", Decimal("0.05")),
)

SAMPLE_CAVEAT = "n={n}; 1 row = {pp:.1f} pp"


@dataclass(frozen=True)
class LabelledRequest:
    """One row of `sample_requests.csv`: the inputs plus its ground-truth label."""

    request_id: str
    user_id: str
    request_date: date
    requested_amount: Decimal
    desired_completion_date: date | None
    allows_partial_payment: bool
    request_text: str
    expected: dict[str, str]


@dataclass
class FieldTally:
    name: str
    hits: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return 0.0 if self.total == 0 else self.hits / self.total


@dataclass
class NumericTally:
    """Error statistics for `amount_safe_to_pay`.

    Relative error is measured against the EXPECTED amount -- the quantity being
    predicted. Dividing by `requested_amount` flatters the result badly: 59,208
    against a label of 1,425,000 is a 96% miss, but reads as 2.3% of a
    60,496,000 request.
    """

    total: int = 0
    exact: int = 0
    relative_errors: list[Decimal] = field(default_factory=list)
    absolute_errors: list[Decimal] = field(default_factory=list)

    def within(self, tolerance: Decimal) -> int:
        return sum(1 for e in self.relative_errors if e <= tolerance)

    @property
    def mean_absolute_error(self) -> Decimal:
        if not self.absolute_errors:
            return Decimal(0)
        return sum(self.absolute_errors, Decimal(0)) / Decimal(len(self.absolute_errors))

    @property
    def median_relative_error(self) -> Decimal:
        if not self.relative_errors:
            return Decimal(0)
        ordered = sorted(self.relative_errors)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


@dataclass
class RowComparison:
    request_id: str
    expected: dict[str, str]
    actual: dict[str, str]
    error: str | None = None

    def matches(self, field_name: str) -> bool:
        return self.expected.get(field_name, "") == self.actual.get(field_name, "")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_labelled_requests(config: Config) -> list[LabelledRequest]:
    """Read `sample_requests.csv` -- inputs and ground-truth labels together."""
    with config.paths.sample_requests_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    labelled: list[LabelledRequest] = []
    for row in rows:
        completion = (row.get("desired_completion_date") or "").strip()
        labelled.append(
            LabelledRequest(
                request_id=row["request_id"],
                user_id=row["user_id"],
                request_date=date.fromisoformat(row["request_date"].strip()),
                requested_amount=parse_money(row["requested_amount"]),
                desired_completion_date=date.fromisoformat(completion) if completion else None,
                allows_partial_payment=(row.get("allows_partial_payment") or "").strip().lower()
                == "true",
                request_text=row.get("request_text", ""),
                expected={
                    name: (row.get(name) or "").strip()
                    for name in (
                        NUMERIC_FIELD,
                        *CATEGORICAL_FIELDS,
                        *STRUCTURAL_FIELDS,
                        UNSCORED_FIELD,
                    )
                },
            )
        )
    return labelled


Predictor = Callable[[LabelledRequest], OutputRow]


def stub_predictor(request: LabelledRequest) -> OutputRow:
    """Returns SAFE_DEFAULT for every row. Exercises the harness, scores badly."""
    return safe_default_row(
        request.request_id,
        request_date=request.request_date,
        requested_amount=request.requested_amount,
    )


def pipeline_predictor(config: Config) -> Predictor:
    """The real deterministic pipeline, one labelled request at a time."""
    from src.plans import RequestRow
    from src.run import build_projection_rule, decide_request, load_dataset, to_output_row

    dataset = load_dataset(config)
    rule = build_projection_rule(config)

    def predict(request: LabelledRequest) -> OutputRow:
        row = RequestRow(
            request_id=request.request_id,
            user_id=request.user_id,
            request_date=request.request_date,
            request_type="",
            requested_amount=request.requested_amount,
            desired_completion_date=request.desired_completion_date,
            allows_partial_payment=request.allows_partial_payment,
            request_text=request.request_text,
        )
        decision, _ = decide_request(row, dataset, config, rule)
        return to_output_row(row, decision)

    return predict


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def compare_rows(requests: Sequence[LabelledRequest], predictor: Predictor) -> list[RowComparison]:
    """Run the predictor over every labelled row and collect expected vs actual.

    A predictor that raises does not abort the run; the failure is recorded and
    every field of that row counts as a miss.
    """
    comparisons: list[RowComparison] = []
    for request in requests:
        try:
            actual = predictor(request).to_csv_dict()
            error = None
        except Exception as exc:  # noqa: BLE001 - a broken predictor must still report
            actual, error = {}, f"{type(exc).__name__}: {exc}"
        comparisons.append(
            RowComparison(
                request_id=request.request_id,
                expected=request.expected,
                actual=actual,
                error=error,
            )
        )
    return comparisons


def tally_categoricals(comparisons: Sequence[RowComparison]) -> dict[str, FieldTally]:
    tallies = {name: FieldTally(name=name) for name in (*CATEGORICAL_FIELDS, *STRUCTURAL_FIELDS)}
    for comparison in comparisons:
        for name, tally in tallies.items():
            tally.total += 1
            if comparison.error is None and comparison.matches(name):
                tally.hits += 1
    return tallies


def tally_numeric(comparisons: Sequence[RowComparison]) -> NumericTally:
    tally = NumericTally()
    for comparison in comparisons:
        tally.total += 1
        expected_text = comparison.expected.get(NUMERIC_FIELD, "")
        actual_text = comparison.actual.get(NUMERIC_FIELD, "")
        if comparison.error is not None or not expected_text or not actual_text:
            tally.relative_errors.append(Decimal(1))
            continue

        expected, actual = parse_money(expected_text), parse_money(actual_text)
        absolute = abs(expected - actual)
        tally.absolute_errors.append(absolute)
        if expected_text == actual_text:
            tally.exact += 1
        if expected == 0:
            tally.relative_errors.append(Decimal(0) if absolute <= MONEY_EPSILON else Decimal(1))
        else:
            tally.relative_errors.append(absolute / abs(expected))
    return tally


def confusion_matrix(
    comparisons: Sequence[RowComparison], field_name: str
) -> dict[tuple[str, str], int]:
    """(expected, predicted) -> count."""
    matrix: dict[tuple[str, str], int] = {}
    for comparison in comparisons:
        key = (
            comparison.expected.get(field_name, "") or "<blank>",
            comparison.actual.get(field_name, "") or "<error>",
        )
        matrix[key] = matrix.get(key, 0) + 1
    return matrix


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _caveat(n: int) -> str:
    return SAMPLE_CAVEAT.format(n=n, pp=(100.0 / n if n else 0.0))


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def _truncate(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 3] + "..."


def _print_confusion(comparisons: Sequence[RowComparison], field_name: str) -> None:
    matrix = confusion_matrix(comparisons, field_name)
    labels = sorted({k[0] for k in matrix} | {k[1] for k in matrix})
    cell = 13

    print()
    print(f"  CONFUSION MATRIX -- {field_name}   (row = expected, column = predicted)")
    print(" " * 26 + "".join(f"{x[:cell - 1]:>{cell}}" for x in labels))
    for expected in labels:
        total = sum(v for (e, _a), v in matrix.items() if e == expected)
        if total == 0:
            continue
        cells = "".join(
            f"{matrix.get((expected, actual), 0) or '.':>{cell}}" for actual in labels
        )
        print(f"    {expected[:22]:<22}{cells}   ({total})")


def print_report(comparisons: Sequence[RowComparison], *, label: str, verbose: bool = True) -> None:
    """Per-field aggregates, confusion matrices, per-row table, explanations."""
    n = len(comparisons)
    caveat = _caveat(n)

    print()
    print("=" * 104)
    print(f"  SAMPLE SCORE -- ALL SEVEN COLUMNS  --  predictor: {label}")
    print("=" * 104)
    print(
        f"  Every aggregate below is over {n} labelled rows. "
        f"One row is worth {100.0 / n if n else 0:.1f} percentage points."
    )
    print("  Treat differences smaller than a few points as noise, not signal.")
    print()
    print("  WHAT THIS SAMPLE CANNOT MEASURE")
    print("  10 of the 11 evaluation-set rows carrying a document image lie in the")
    print("  unlabelled 225, so the extraction layer's contribution is INVISIBLE here.")
    print("  It is reported instead by its proxy: unresolved blank amounts, 11 -> 0.")
    print("  Status rule 4 fires on 8 of 250 evaluation rows and on 0 labelled rows,")
    print("  so that branch is unvalidated by anything below.")

    failures = [c for c in comparisons if c.error is not None]
    if failures:
        print()
        print(f"  !! {len(failures)} row(s) raised and count as a miss on every field:")
        for comparison in failures[:10]:
            print(f"     {comparison.request_id}: {comparison.error}")

    numeric = tally_numeric(comparisons)
    tallies = tally_categoricals(comparisons)

    print()
    print("-" * 104)
    print("  PER-FIELD ACCURACY")
    print("-" * 104)
    print(f"  {'column':<34}{'metric':<26}{'value':>12}   caveat")
    print(
        f"  {NUMERIC_FIELD:<34}{'exact string match':<26}"
        f"{_pct(numeric.exact / n if n else 0):>12}   {numeric.exact}/{n}, {caveat}"
    )
    for name, tolerance in NEAR_TOLERANCES:
        hits = numeric.within(tolerance)
        print(
            f"  {'':<34}{'within ' + name:<26}{_pct(hits / n if n else 0):>12}"
            f"   {hits}/{n}, {caveat}"
        )
    print(
        f"  {'':<34}{'median relative error':<26}"
        f"{_pct(float(numeric.median_relative_error)):>12}   vs the label amount"
    )
    print(
        f"  {'':<34}{'mean absolute error':<26}"
        f"{format_safe_amount(numeric.mean_absolute_error.quantize(Decimal('0.01'))):>12}"
        f"   mixed currencies -- read per-row"
    )

    print()
    for name in (*CATEGORICAL_FIELDS, *STRUCTURAL_FIELDS):
        tally = tallies[name]
        print(
            f"  {name:<34}{'exact match':<26}{_pct(tally.accuracy):>12}"
            f"   {tally.hits}/{tally.total}, {caveat}"
        )
    print(f"  {UNSCORED_FIELD:<34}{'not scored':<26}{'--':>12}   printed below for reading")

    for name in CATEGORICAL_FIELDS:
        _print_confusion(comparisons, name)

    if not verbose:
        print()
        return

    print()
    print("-" * 104)
    print("  PER-ROW  ('=' means match)")
    print("-" * 104)
    for comparison in comparisons:
        print()
        print(f"  {comparison.request_id}")
        if comparison.error:
            print(f"     PREDICTOR FAILED: {comparison.error}")
        for name in (NUMERIC_FIELD, *CATEGORICAL_FIELDS, *STRUCTURAL_FIELDS):
            expected = comparison.expected.get(name, "")
            actual = comparison.actual.get(name, "")
            mark = "=" if expected == actual else "X"
            print(f"     {mark} {name:<32} exp {_truncate(expected, 48)}")
            if mark == "X":
                print(f"       {'':<32} got {_truncate(actual, 48)}")

    print()
    print("-" * 104)
    print("  DECISION EXPLANATIONS  (no automated score -- judge these by reading)")
    print("-" * 104)
    for comparison in comparisons:
        print()
        print(f"  {comparison.request_id}")
        print(f"     exp  {_truncate(comparison.expected.get(UNSCORED_FIELD, ''), 92)}")
        print(f"     got  {_truncate(comparison.actual.get(UNSCORED_FIELD, ''), 92)}")

    print()
    print("=" * 104)
    print(f"  END OF REPORT  --  {caveat}")
    print("=" * 104)
    print()


def score(
    predictor: Predictor,
    *,
    config: Config | None = None,
    limit: int | None = None,
    label: str = "predictor",
    verbose: bool = True,
) -> list[RowComparison]:
    """Load the labelled rows, run `predictor`, print the report, return the rows."""
    cfg = config or default_config()
    requests = load_labelled_requests(cfg)
    if limit is not None:
        requests = requests[:limit]
    comparisons = compare_rows(requests, predictor)
    print_report(comparisons, label=label, verbose=verbose)
    return comparisons


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub", action="store_true", help="score the SAFE_DEFAULT stub")
    parser.add_argument("--rows", type=int, default=None, help="limit to the first N rows")
    parser.add_argument("--quiet", action="store_true", help="aggregates and matrices only")
    args = parser.parse_args(argv)

    cfg = default_config()
    if args.stub:
        score(
            stub_predictor,
            config=cfg,
            limit=args.rows,
            label="stub (SAFE_DEFAULT for every row)",
            verbose=not args.quiet,
        )
    else:
        score(
            pipeline_predictor(cfg),
            config=cfg,
            limit=args.rows,
            label="deterministic pipeline",
            verbose=not args.quiet,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
