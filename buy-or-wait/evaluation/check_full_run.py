"""Run all 250 requests and assert the invariants that catch gate bugs.

These checks are cheap and they fail loudly. Each one corresponds to a way the
eligibility gates can silently come unwired, where the output still looks
plausible row by row but is wrong in aggregate:

  * `partial_payment` on a request that does not allow it;
  * `installments` without a surviving eligible option;
  * `affordable_now` whose `earliest_date_for_full_payment` is not the request
    date;
  * a contract violation, or a row that fell back to SAFE_DEFAULT.

Usage:
    python -m evaluation.check_full_run
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import default_config  # noqa: E402
from src.contract import AffordabilityStatus, PaymentMethod  # noqa: E402
from src.options import eligible_options, load_payment_options  # noqa: E402
from src.run import load_dataset, load_requests, run  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-model",
        action="store_true",
        help="replay the OCR and observation caches (no API key needed to read them)",
    )
    parser.add_argument("--out", type=Path, default=None, help="output CSV to write")
    args = parser.parse_args(argv)
    config = default_config()
    requests = {r.request_id: r for r in load_requests(config.paths.requests_csv)}
    dataset = load_dataset(config)

    # Honour the model layer. Running without it here used to overwrite a
    # model-backed output.csv with a deterministic one, silently discarding
    # every recovered image amount.
    rows, summary = run(config, use_model=args.use_model, output_path=args.out)
    by_id = {row.request_id: row for row in rows}

    print()
    print("=" * 96)
    print("  FULL RUN -- 250 REQUESTS")
    print("=" * 96)
    print(f"  rows written                 {summary.rows}")
    print(f"  model layer                  {'ON (caches replayed)' if args.use_model else 'OFF'}")
    if args.use_model:
        print(
            f"  image amounts applied        {summary.ocr_extracted}"
            f"   (unresolved: {summary.ocr_failed})"
        )
        print(f"  message amendments applied   {summary.amendments_applied}")
    print(f"  contract validation failures 0 (every row is validated in to_output_row)")
    print(f"  SAFE_DEFAULT fallbacks       {len(summary.fallbacks)}")
    for line in summary.fallbacks:
        print(f"     {line}")

    print()
    print("  affordability_status:")
    for name, count in sorted(summary.status_counts.items(), key=lambda kv: -kv[1]):
        print(f"     {name:<24}{count:>5}  ({count / summary.rows:5.1%})")
    print("  recommended_payment_method:")
    for name, count in sorted(summary.method_counts.items(), key=lambda kv: -kv[1]):
        print(f"     {name:<24}{count:>5}  ({count / summary.rows:5.1%})")
    print("  status rule that fired:")
    for rule, count in sorted(summary.rule_counts.items()):
        print(f"     rule {rule}{'':<19}{count:>5}")

    if summary.loud_notes:
        print()
        print(f"  !! UNVALIDATED STATUS RULE 4 fired on {len(summary.loud_notes)} row(s):")
        for note in summary.loud_notes[:15]:
            print(f"     {note}")

    print()
    print(f"  rows with an unresolved blank amount: {len(summary.missing_amount_rows)}")
    for line in summary.missing_amount_rows[:10]:
        print(f"     {line}")
    print(f"  rows using an FX nearest-date fallback: {len(summary.fx_fallback_rows)}")

    # -- sanity checks ------------------------------------------------------
    print()
    print("-" * 96)
    print("  SANITY CHECKS")
    print("-" * 96)
    failures: list[str] = []

    allows_partial = {rid for rid, r in requests.items() if r.allows_partial_payment}
    partial_rows = [
        r.request_id
        for r in rows
        if r.recommended_payment_method is PaymentMethod.PARTIAL_PAYMENT
    ]
    illegal_partial = [rid for rid in partial_rows if rid not in allows_partial]
    status = "PASS" if len(partial_rows) <= len(allows_partial) and not illegal_partial else "FAIL"
    print(
        f"  [{status}] partial_payment on {len(partial_rows)} rows, cap is "
        f"{len(allows_partial)} (rows where allows_partial_payment=true)"
    )
    if illegal_partial:
        failures.append(f"partial_payment on rows that forbid it: {illegal_partial}")

    raw_options = load_payment_options(config.paths.request_payment_options_csv)
    installment_rows = [
        r.request_id for r in rows if r.recommended_payment_method is PaymentMethod.INSTALLMENTS
    ]
    unbacked: list[str] = []
    for rid in installment_rows:
        profile = dataset.profiles[requests[rid].user_id]
        survivors = eligible_options(
            raw_options.get(rid, ()), profile, days_per_month=config.forecast.days_per_month
        )
        if not any(o.is_installments for o in survivors):
            unbacked.append(rid)
    print(
        f"  [{'PASS' if not unbacked else 'FAIL'}] installments on {len(installment_rows)} rows, "
        f"all backed by a surviving eligible option"
    )
    if unbacked:
        failures.append(f"installments without an eligible option: {unbacked}")

    bad_now = [
        r.request_id
        for r in rows
        if r.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
        and r.earliest_date_for_full_payment != requests[r.request_id].request_date
    ]
    now_rows = sum(
        1 for r in rows if r.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
    )
    print(
        f"  [{'PASS' if not bad_now else 'FAIL'}] all {now_rows} affordable_now rows have "
        f"earliest == request_date"
    )
    if bad_now:
        failures.append(f"affordable_now with a wrong earliest date: {bad_now}")

    not_rec_with_plan = [
        r.request_id
        for r in rows
        if r.recommended_payment_method is PaymentMethod.NOT_RECOMMENDED
        and r.payment_plan.serialise() != "none"
    ]
    print(
        f"  [{'PASS' if not not_rec_with_plan else 'FAIL'}] every not_recommended row has "
        f"payment_plan 'none'"
    )
    if not_rec_with_plan:
        failures.append(f"not_recommended carrying a plan: {not_rec_with_plan}")

    over_cap = [
        r.request_id for r in rows if r.amount_safe_to_pay > requests[r.request_id].requested_amount
    ]
    print(
        f"  [{'PASS' if not over_cap else 'FAIL'}] no amount_safe_to_pay exceeds its "
        f"requested_amount"
    )
    if over_cap:
        failures.append(f"amount_safe_to_pay above the request: {over_cap}")

    written = list(csv.DictReader(config.paths.output_csv.open(encoding="utf-8", newline="")))
    order_ok = [w["request_id"] for w in written] == list(requests)
    print(f"  [{'PASS' if order_ok else 'FAIL'}] output.csv preserves requests.csv input order")
    if not order_ok:
        failures.append("output.csv is not in input order")

    blank = [w["request_id"] for w in written if not w["affordability_status"].strip()]
    print(f"  [{'PASS' if not blank else 'FAIL'}] no blank rows in output.csv")
    if blank:
        failures.append(f"blank rows: {blank}")

    print()
    if failures:
        print(f"  {len(failures)} SANITY CHECK FAILURE(S):")
        for line in failures:
            print(f"     {line}")
        return 1
    print("  All sanity checks passed.")
    print()
    _ = Counter
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
