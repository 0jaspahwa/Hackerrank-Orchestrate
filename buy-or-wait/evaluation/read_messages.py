"""Read the message batches that are not yet cached. Messages only.

No images are touched here. Users already answered in the observation cache --
including those cached as `no_change` -- are not re-read, so a second run costs
nothing.

Credentials come from the environment in priority order (GEMINI_API_KEY, then
GEMINI_API_KEY_2); same provider, same model, same validator.

Usage:
    python -m evaluation.read_messages --dry-run     # list the batches, call nothing
    python -m evaluation.read_messages               # make the calls
    python -m evaluation.read_messages --regenerate  # ... then rewrite output.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, default_config  # noqa: E402
from src.ledger import FinancialEvent, load_events  # noqa: E402
from src.observe import (  # noqa: E402
    Message,
    is_worth_reading,
    load_amendment_cache,
    load_messages,
    observe_messages,
)
from src.usage import UsageRecorder  # noqa: E402

#: Measured per USER, so the estimate follows whatever batch size is in force.
#: Gemini: ~242 in / ~7 out per user. Groq's gpt-oss-20b emits reasoning tokens
#: before its answer, so its output is far larger -- ~1,000 per call observed.
OBSERVED_INPUT_TOKENS_PER_USER = 242
OBSERVED_OUTPUT_TOKENS_PER_USER = 200


def index_events(config: Config) -> dict[str, tuple[FinancialEvent, ...]]:
    by_user: dict[str, list[FinancialEvent]] = {}
    for event in load_events(config.paths.financial_events_csv):
        by_user.setdefault(event.user_id, []).append(event)
    return {user: tuple(rows) for user, rows in by_user.items()}


def pending_users(config: Config) -> tuple[list[str], int, int]:
    """(users still to read, messages kept by the pre-filter, messages seen)."""
    messages = load_messages(config.paths.messages_csv)
    cached = load_amendment_cache(config.paths.artifacts_dir / config.observe.cache_filename)

    seen = sum(len(v) for v in messages.values())
    kept = 0
    todo: list[str] = []
    for user_id, rows in sorted(messages.items()):
        worth = [m for m in rows if is_worth_reading(m, config.observe)]
        kept += len(worth)
        if worth and user_id not in cached:
            todo.append(user_id)
    return todo, kept, seen


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list batches; call nothing")
    parser.add_argument("--regenerate", action="store_true", help="rewrite output.csv after")
    args = parser.parse_args(argv)
    config = default_config()

    todo, kept, seen = pending_users(config)
    # Providers read different amounts per call; report the one that will serve.
    use_groq_size = config.observe.provider == config.groq.provider
    size = config.groq.batch_size if use_groq_size else config.observe.batch_size
    batches = [todo[i : i + size] for i in range(0, len(todo), size)]
    cached = load_amendment_cache(config.paths.artifacts_dir / config.observe.cache_filename)

    print()
    print("=" * 108)
    print("  MESSAGE RECONCILIATION -- uncached users only, no images")
    print("=" * 108)
    print(f"  messages in file        {seen}")
    print(f"  kept by the pre-filter  {kept}   (skipped {seen - kept})")
    print(f"  already cached          {len(cached)} user(s)")
    print(f"  users to read           {len(todo)}")
    print(f"  batches at {size}/call      {len(batches)}")
    print(f"  images                  0   (the extraction stage is not run here)")
    # Report the provider that will actually serve this stage. Vision stays on
    # Gemini; the message stage is a config switch.
    use_groq = config.observe.provider == config.groq.provider
    if use_groq:
        provider, model_name = config.groq.provider, config.groq.text_model
        key_name = config.groq.api_key_env_var
        credentials = [key_name] if config.groq.is_enabled() else []
    else:
        provider, model_name = config.model.provider, config.model.text_model
        key_name = config.model.api_key_env_var
        credentials = [name for name, _ in config.model.credentials()]
    print(f"  provider / model        {provider} / {model_name}")
    print(f"  credential ({key_name}){'':<4}{credentials or 'NOT SET'}")

    print()
    print("  BATCH LIST")
    for index, batch in enumerate(batches, start=1):
        print(f"     batch {index:>2}: {', '.join(batch)}")

    est_in = len(todo) * OBSERVED_INPUT_TOKENS_PER_USER
    est_out = len(todo) * OBSERVED_OUTPUT_TOKENS_PER_USER
    print()
    print(f"  estimated tokens: ~{est_in:,} in + ~{est_out:,} out = ~{est_in + est_out:,} total")
    print(
        f"  (from the {OBSERVED_INPUT_TOKENS_PER_USER}/{OBSERVED_OUTPUT_TOKENS_PER_USER} "
        f"per-user average actually observed on this dataset)"
    )
    print(
        f"  pacing {config.groq.inter_call_sleep_seconds:g}s/call -> "
        f"~{60 / max(config.groq.inter_call_sleep_seconds, 1) * (est_in + est_out) / max(len(batches), 1):,.0f} TPM "
        f"against a measured 8,000 ceiling"
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
    result = observe_messages(
        load_messages(config.paths.messages_csv),
        index_events(config),
        config=config,
        recorder=recorder,
    )

    print()
    print("-" * 108)
    print("  RESULT")
    print("-" * 108)
    print(f"  batches called          {result.batches}")
    print(f"  replayed from cache     {result.from_cache}")
    print(f"  users read, no change   {result.read_no_change}")
    print(f"  amendments ACCEPTED     {len(result.amendments)}")
    print(f"  amendments REJECTED     {len(result.discarded)}")
    for line in result.discarded:
        print(f"     rejected: {line}")
    for line in result.errors:
        print(f"     error: {line[:140]}")

    if result.amendments:
        print()
        print("  ACCEPTED AMENDMENTS")
        for user_id, amendment in sorted(result.amendments.items()):
            print(
                f"     {user_id:10s} {amendment.action.value:18s} "
                f"{amendment.applies_to.value:22s} {amendment.series_key[:24]:25s} "
                f"{amendment.new_amount if amendment.new_amount is not None else '-'}"
            )
            print(f"     {'':10s} evidence: {amendment.quoted_evidence[:80]!r}")

    print()
    print(
        f"  calls this run {recorder.calls}   tokens {recorder.input_tokens:,} in / "
        f"{recorder.output_tokens:,} out"
    )

    if args.regenerate:
        from src.run import run

        rows, summary = run(config, use_model=True)
        print()
        print(
            f"  output.csv regenerated: {summary.rows} rows, "
            f"{summary.ocr_extracted} image amounts, "
            f"{summary.amendments_applied} amendments, "
            f"{len(summary.fallbacks)} fallbacks"
        )
        _ = rows
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
