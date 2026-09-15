"""Extract the blank-amount images that are not yet cached. Images only.

This calls NOTHING that is already decided in the cache, and it never touches
the message batches -- the observation stage is not run here at all.

Credentials come from the environment in priority order (GEMINI_API_KEY, then
GEMINI_API_KEY_2). Same provider, same model, same prompt, same validator; only
the credential differs, so a quota-exhausted primary falls through rather than
stranding the run.

Usage:
    python -m evaluation.extract_images --dry-run     # list the calls, cost nothing
    python -m evaluation.extract_images               # make them
    python -m evaluation.extract_images --regenerate  # ... then rewrite output.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, default_config  # noqa: E402
from src.ledger import FinancialEvent, load_events  # noqa: E402
from src.ocr import (  # noqa: E402
    ExtractedAmount,
    extract_all,
    load_cache,
    load_image_links,
    usable_amounts,
)
from src.usage import UsageRecorder  # noqa: E402

#: Observed from the 14 calls that completed before the primary quota ran out:
#: ~1,485 prompt tokens and ~72 output tokens per image. Used for the estimate
#: only -- the report always shows what was actually spent.
OBSERVED_INPUT_TOKENS_PER_IMAGE = 1485
OBSERVED_OUTPUT_TOKENS_PER_IMAGE = 72


def index_events(config: Config) -> dict[str, tuple[FinancialEvent, ...]]:
    by_user: dict[str, list[FinancialEvent]] = {}
    for event in load_events(config.paths.financial_events_csv):
        by_user.setdefault(event.user_id, []).append(event)
    return {user: tuple(rows) for user, rows in by_user.items()}


def pending(config: Config) -> list[tuple[str, str, str]]:
    """(event_id, image_id, description) for every image not yet decided."""
    cache = load_cache(config.paths.artifacts_dir / config.vision.cache_filename)
    links = load_image_links(config.paths.images_csv)
    events = {
        row["event_id"]: row
        for row in csv.DictReader(
            config.paths.financial_events_csv.open(encoding="utf-8", newline="")
        )
    }
    rows = [
        (event_id, link.image_id, events[event_id]["description"])
        for event_id, link in links.items()
        if event_id not in cache
    ]
    return sorted(rows, key=lambda r: int(r[0].split("_")[1]))


def print_table(results: dict[str, ExtractedAmount], config: Config) -> None:
    events = {
        row["event_id"]: row
        for row in csv.DictReader(
            config.paths.financial_events_csv.open(encoding="utf-8", newline="")
        )
    }
    print()
    print("-" * 112)
    print("  EXTRACTION RESULTS")
    print("-" * 112)
    print(
        f"  {'event':12s}{'ccy':4s}{'status':12s}{'field_label':30s}{'amount':>14s}"
        f"  {'key':18s}description"
    )
    for event_id in sorted(results, key=lambda e: int(e.split("_")[1])):
        outcome = results[event_id]
        event = events[event_id]
        amount = "-" if outcome.amount is None else f"{outcome.amount:,}"
        print(
            f"  {event_id:12s}{event['currency']:4s}{outcome.status.value:12s}"
            f"{outcome.field_label[:29]:30s}{amount:>14s}  "
            f"{(outcome.key_label or '-'):18s}{event['description'][:28]}"
        )
        if outcome.rejection_reason:
            print(f"  {'':30s}! {outcome.rejection_reason[:74]}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="list the calls and estimated tokens; call nothing"
    )
    parser.add_argument(
        "--regenerate", action="store_true", help="rewrite output.csv from the refreshed cache"
    )
    args = parser.parse_args(argv)
    config = default_config()

    todo = pending(config)
    cache = load_cache(config.paths.artifacts_dir / config.vision.cache_filename)
    resolved_before = len(usable_amounts(cache))

    print()
    print("=" * 112)
    print("  IMAGE EXTRACTION -- uncached only, no message batches")
    print("=" * 112)
    print(f"  images total            16")
    print(f"  already decided (cache) {len(cache)}")
    print(f"  to call                 {len(todo)}")
    print(f"  message batches         0   (the observation stage is not run here)")
    credentials = [name for name, _ in config.model.credentials()]
    print(f"  credentials available   {credentials or 'NONE'}")
    print(f"  provider / model        {config.model.provider} / {config.model.vision_model}")
    print(f"  resolved image amounts  {resolved_before} (before)")

    print()
    print("  CALL LIST")
    for event_id, image_id, description in todo:
        print(f"     {event_id:12s} {image_id:10s} {description}")

    est_in = len(todo) * OBSERVED_INPUT_TOKENS_PER_IMAGE
    est_out = len(todo) * OBSERVED_OUTPUT_TOKENS_PER_IMAGE
    print()
    print(
        f"  estimated tokens: ~{est_in:,} in + ~{est_out:,} out = ~{est_in + est_out:,} total"
    )
    print(
        f"  (from the {OBSERVED_INPUT_TOKENS_PER_IMAGE}/{OBSERVED_OUTPUT_TOKENS_PER_IMAGE} "
        f"per-image average actually observed on this dataset)"
    )

    if args.dry_run:
        print()
        print("  DRY RUN -- nothing was called.")
        print()
        return 0

    if not credentials:
        print()
        print("  No credential in the environment. Nothing called; nothing changed.")
        print()
        return 1

    recorder = UsageRecorder(path=config.paths.artifacts_dir / "usage_metrics.jsonl")
    results = extract_all(
        index_events(config),
        load_image_links(config.paths.images_csv),
        config=config,
        recorder=recorder,
    )
    print_table(results, config)

    resolved_after = len(usable_amounts(results))
    print()
    print(f"  resolved image amounts  {resolved_before} -> {resolved_after}")
    print(f"  unresolved blanks       {16 - resolved_before} -> {16 - resolved_after}")
    print(
        f"  calls this run {recorder.calls}   tokens {recorder.input_tokens:,} in / "
        f"{recorder.output_tokens:,} out"
    )
    by_key: dict[str, int] = {}
    for record in recorder.records:
        if record.ok:
            by_key[record.key_label] = by_key.get(record.key_label, 0) + 1
    if by_key:
        print(f"  successful calls by credential: {by_key}")

    if args.regenerate:
        from src.run import run

        rows, summary = run(config, use_model=True)
        print()
        print(
            f"  output.csv regenerated: {summary.rows} rows, "
            f"{summary.ocr_extracted} image amounts applied, "
            f"{len(summary.fallbacks)} fallbacks"
        )
        _ = rows
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
