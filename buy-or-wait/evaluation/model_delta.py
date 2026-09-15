"""Before/after report for the model layer.

BEFORE is the deterministic pipeline with no image amounts and no message
amendments. AFTER is the same pipeline reading the OCR and observation caches.
Both run here, in-process, over the same 25 labelled rows, so the delta is
attributable to the model layer and nothing else.

Usage:
    python -m evaluation.model_delta
"""

from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.score import (  # noqa: E402
    CATEGORICAL_FIELDS,
    NUMERIC_FIELD,
    STRUCTURAL_FIELDS,
    compare_rows,
    load_labelled_requests,
    tally_categoricals,
    tally_numeric,
)
from src.config import Config, default_config  # noqa: E402
from src.contract import OutputRow  # noqa: E402
from src.observe import load_amendment_cache  # noqa: E402
from src.ocr import load_cache, usable_amounts  # noqa: E402
from src.plans import RequestRow  # noqa: E402
from src.run import build_projection_rule, decide_request, load_dataset, to_output_row  # noqa: E402
from src.usage import UsageRecorder  # noqa: E402

#: Labelled rows that carry an image, and what each one needs.
IMAGE_ROWS = ("request_03", "request_16", "request_17", "request_19", "request_20")
MESSAGE_ROWS = ("request_08", "request_10", "request_24")


def predictor_for(config: Config, *, with_model: bool):
    """A predictor over the labelled rows, with or without the model layer."""
    recorder = UsageRecorder(path=config.paths.artifacts_dir / "delta_scratch.jsonl")
    dataset = load_dataset(config, recorder=recorder, use_model=with_model)
    if not with_model:
        dataset = dataset.model_copy(update={"image_amounts": {}, "amendments": {}})
    rule = build_projection_rule(config)

    def predict(request) -> OutputRow:
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

    return predict, dataset


def snapshot(comparisons) -> dict[str, int | float]:
    numeric = tally_numeric(comparisons)
    tallies = tally_categoricals(comparisons)
    return {
        "amount_exact": numeric.exact,
        "amount_1pct": numeric.within(Decimal("0.01")),
        "amount_5pct": numeric.within(Decimal("0.05")),
        "median_rel": float(numeric.median_relative_error),
        **{name: tallies[name].hits for name in (*CATEGORICAL_FIELDS, *STRUCTURAL_FIELDS)},
    }


def _delta(before: int | float, after: int | float, invert: bool = False) -> str:
    diff = after - before
    if abs(diff) < 1e-9:
        return "  ="
    good = diff < 0 if invert else diff > 0
    return f"{'+' if diff > 0 else ''}{diff:g}{'  ' if good else ' !'}"


def print_extraction_table(config: Config) -> None:
    events = {
        row["event_id"]: row
        for row in csv.DictReader(
            config.paths.financial_events_csv.open(encoding="utf-8", newline="")
        )
    }
    images = {
        row["related_event_id"]: row
        for row in csv.DictReader(config.paths.images_csv.open(encoding="utf-8", newline=""))
    }
    cache = load_cache(config.paths.artifacts_dir / config.vision.cache_filename)
    labelled = set(IMAGE_ROWS)

    print()
    print("=" * 108)
    print("  IMAGE EXTRACTION -- all 16 blank-amount events")
    print("=" * 108)
    print(
        f"  {'event':12s}{'L':2s}{'ccy':4s}{'status':12s}{'field_label':30s}"
        f"{'amount':>14s}  description"
    )
    for event_id in sorted(cache, key=lambda e: int(e.split("_")[1])):
        outcome = cache[event_id]
        event = events[event_id]
        link = images.get(event_id, {})
        mark = "*" if link.get("request_id") in labelled else " "
        amount = "-" if outcome.amount is None else f"{outcome.amount:,}"
        print(
            f"  {event_id:12s}{mark:2s}{event['currency']:4s}{outcome.status.value:12s}"
            f"{outcome.field_label[:29]:30s}{amount:>14s}  {event['description'][:30]}"
        )
        if outcome.rejection_reason:
            print(f"  {'':30s}! {outcome.rejection_reason[:70]}")

    missing = [e for e in images if e not in cache]
    usable = sum(1 for o in cache.values() if o.usable)
    print()
    print(
        f"  extracted {usable}/16   not_visible "
        f"{sum(1 for o in cache.values() if o.status.value == 'not_visible')}   "
        f"failed {sum(1 for o in cache.values() if o.status.value == 'failed')}   "
        f"never attempted {len(missing)}"
    )
    print(f"  cross-checked against Tesseract: {sum(1 for o in cache.values() if o.cross_checked)}/16")
    print("  (* = one of the five labelled rows that carries an image)")


def print_observation_summary(config: Config) -> None:
    cached = load_amendment_cache(config.paths.artifacts_dir / config.observe.cache_filename)
    real = {u: a for u, a in cached.items() if a.changes_anything}
    print()
    print("-" * 108)
    print("  MESSAGE RECONCILIATION")
    print("-" * 108)
    print(f"  users read and cached : {len(cached)}")
    print(f"  amendments that change something : {len(real)}")
    for user_id, amendment in sorted(real.items()):
        print(
            f"     {user_id:10s} {amendment.action.value:18s} "
            f"{amendment.applies_to.value:22s} {amendment.series_key[:26]:27s} "
            f"{amendment.new_amount if amendment.new_amount is not None else '-'}"
        )
        print(f"     {'':10s} evidence: {amendment.quoted_evidence[:78]!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    config = default_config()

    print_extraction_table(config)
    print_observation_summary(config)

    requests = load_labelled_requests(config)
    before_fn, _ = predictor_for(config, with_model=False)
    after_fn, dataset = predictor_for(config, with_model=True)

    before = snapshot(compare_rows(requests, before_fn))
    after_rows = compare_rows(requests, after_fn)
    after = snapshot(after_rows)

    print()
    print("-" * 108)
    print("  SEVEN-COLUMN SCORE -- BEFORE (deterministic) vs AFTER (with model layer)")
    print("-" * 108)
    print(f"  {'column / metric':<40}{'before':>9}{'after':>9}{'delta':>9}   n=25, 1 row = 4.0 pp")
    order = [
        ("amount_safe_to_pay  exact", "amount_exact", False),
        ("amount_safe_to_pay  within 1%", "amount_1pct", False),
        ("amount_safe_to_pay  within 5%", "amount_5pct", False),
        ("affordability_status", "affordability_status", False),
        ("recommended_payment_method", "recommended_payment_method", False),
        ("payment_plan", "payment_plan", False),
        ("earliest_date_for_full_payment", "earliest_date_for_full_payment", False),
        ("spending_changes_needed", "spending_changes_needed", False),
    ]
    for label, key, invert in order:
        print(
            f"  {label:<40}{before[key]:>7}/25{after[key]:>7}/25"
            f"{_delta(before[key], after[key], invert):>9}"
        )
    print(
        f"  {'median relative error':<40}"
        f"{before['median_rel'] * 100:>8.1f}%{after['median_rel'] * 100:>8.1f}%"
        f"{_delta(round(before['median_rel'] * 100, 1), round(after['median_rel'] * 100, 1), True):>9}"
    )

    print()
    print("  Rows that carry an image:")
    by_id = {c.request_id: c for c in after_rows}
    for request_id in IMAGE_ROWS:
        comparison = by_id[request_id]
        marks = "".join(
            "=" if comparison.matches(f) else "X"
            for f in (NUMERIC_FIELD, *CATEGORICAL_FIELDS, *STRUCTURAL_FIELDS)
        )
        print(
            f"     {request_id:12s} [{marks}]  exp {comparison.expected[NUMERIC_FIELD]:>14s}"
            f"   got {comparison.actual.get(NUMERIC_FIELD, '-'):>14s}"
        )
    print("     key: amount / status / method / plan / earliest / changes")

    print()
    print("  Rows that need a message:")
    for request_id in MESSAGE_ROWS:
        comparison = by_id[request_id]
        marks = "".join(
            "=" if comparison.matches(f) else "X"
            for f in (NUMERIC_FIELD, *CATEGORICAL_FIELDS, *STRUCTURAL_FIELDS)
        )
        print(
            f"     {request_id:12s} [{marks}]  exp {comparison.expected[NUMERIC_FIELD]:>14s}"
            f"   got {comparison.actual.get(NUMERIC_FIELD, '-'):>14s}"
        )

    print()
    print(f"  image amounts applied : {len(dataset.image_amounts)}")
    print(f"  amendments applied    : {len(dataset.amendments)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
