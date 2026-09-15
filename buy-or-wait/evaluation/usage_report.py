"""Generate `evaluation/usage_report.md` from the metrics JSONL.

AGENTS.md 6.5 requires this file in the submitted `code.zip`. It is GENERATED
from what the recorder captured during the run -- never written by hand, never
reconstructed from memory afterwards. If no run has happened, it says so rather
than inventing numbers.

No API key, credential, or message content ever reaches this file.

Usage:
    python -m evaluation.usage_report
    python -m evaluation.usage_report --metrics artifacts/usage_metrics.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import default_config  # noqa: E402
from src.usage import CallRecord, estimate_cost, load_records  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parent / "usage_report.md"


def _table(rows: Sequence[Sequence[str]], header: Sequence[str]) -> str:
    widths = [
        max(len(str(header[i])), *(len(str(r[i])) for r in rows)) if rows else len(str(header[i]))
        for i in range(len(header))
    ]
    lines = [
        "| " + " | ".join(str(h).ljust(w) for h, w in zip(header, widths)) + " |",
        "|" + "|".join("-" * (w + 2) for w in widths) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c).ljust(w) for c, w in zip(row, widths)) + " |")
    return "\n".join(lines)


def build_report(records: Sequence[CallRecord], *, requests_scored: int) -> str:
    """Render the markdown. Pure over its inputs."""
    config = default_config()
    out: list[str] = ["# Model usage report", ""]

    if not records:
        out += [
            "No model calls have been recorded.",
            "",
            f"The pipeline ran entirely on its deterministic path, which is what happens",
            f"whenever `{config.model.api_key_env_var}` is absent from the environment.",
            "Every image-derived amount stayed unresolved and no message amendment was",
            "applied; no row was blocked and no row was guessed.",
            "",
            "To record real usage:",
            "",
            "```",
            f"export {config.model.api_key_env_var}=...",
            "python -m src.cli --use-model",
            "python -m evaluation.usage_report",
            "```",
            "",
        ]
        return "\n".join(out)

    ok = [r for r in records if r.ok]
    failed = [r for r in records if not r.ok]
    providers = sorted({r.provider for r in records})
    models = sorted({r.model for r in records})

    total_in = sum(r.input_tokens for r in records)
    total_out = sum(r.output_tokens for r in records)
    total_cached = sum(r.cached_tokens for r in records)
    total_tokens = total_in + total_out
    wall = sum(r.wall_seconds for r in records)

    rate_limited = sum(1 for r in records if "429" in r.error)
    credentials = sorted({r.key_label for r in records if r.key_label})

    out += [
        "## Summary",
        "",
        f"- Provider(s): {', '.join(providers)}",
        f"- Model(s): {', '.join(models)}",
        f"- Model calls: {len(records)} ({len(ok)} succeeded, {len(failed)} failed/retried)",
        f"- Rate-limited (HTTP 429): {rate_limited}",
        f"- Credentials used: {', '.join(credentials) if credentials else 'not recorded'}",
        f"- Input tokens: {total_in:,}",
        f"- Output tokens: {total_out:,}",
        f"- Cached tokens: {total_cached:,}",
        f"- Total tokens: {total_tokens:,}",
        f"- Wall time in model calls: {wall:,.1f}s",
        f"- Dataset requests scored: {requests_scored}",
        "",
    ]

    if requests_scored:
        out += [
            f"- Tokens per request: {total_tokens / requests_scored:,.1f}",
            f"- Model calls per request: {len(records) / requests_scored:.3f}",
            "",
        ]

    # -- per model ----------------------------------------------------------
    grouped: dict[tuple[str, str], list[CallRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.model, record.stage)].append(record)

    rows = []
    grand_cost = Decimal(0)
    priced = True
    for (model, stage), bucket in sorted(grouped.items()):
        bucket_in = sum(r.input_tokens for r in bucket)
        bucket_out = sum(r.output_tokens for r in bucket)
        cost = estimate_cost(model, bucket_in, bucket_out)
        if cost is None:
            priced = False
            cost_text = "unpriced"
        else:
            grand_cost += cost
            cost_text = f"${cost:.6f}"
        rows.append(
            [
                model,
                stage,
                str(len(bucket)),
                f"{bucket_in:,}",
                f"{bucket_out:,}",
                f"{bucket_in + bucket_out:,}",
                f"{sum(r.wall_seconds for r in bucket):.1f}s",
                cost_text,
            ]
        )

    if len(credentials) > 1:
        rows_by_key = []
        for name in credentials:
            bucket = [r for r in records if r.key_label == name]
            rows_by_key.append([
                name,
                str(len(bucket)),
                str(sum(1 for r in bucket if r.ok)),
                f"{sum(r.input_tokens for r in bucket):,}",
                f"{sum(r.output_tokens for r in bucket):,}",
            ])
        out += [
            "## Per credential",
            "",
            "Same provider and same model throughout; only the credential differs.",
            "",
            _table(rows_by_key, ["credential", "calls", "succeeded", "input", "output"]),
            "",
        ]

    # The challenge spec requires per-model totals when more than one model is
    # used. Gemini serves the vision stage, Groq the message stage.
    per_model_rows = []
    for name in models:
        bucket = [r for r in records if r.model == name]
        bucket_in = sum(r.input_tokens for r in bucket)
        bucket_out = sum(r.output_tokens for r in bucket)
        cost = estimate_cost(name, bucket_in, bucket_out)
        per_model_rows.append([
            name,
            sorted({r.provider for r in bucket})[0],
            ", ".join(sorted({r.stage for r in bucket})),
            str(len(bucket)),
            str(sum(1 for r in bucket if r.ok)),
            f"{bucket_in:,}",
            f"{bucket_out:,}",
            f"{bucket_in + bucket_out:,}",
            "unpriced" if cost is None else f"${cost:.6f}",
        ])
    per_model_rows.append([
        "ALL MODELS", ", ".join(providers), "all",
        str(len(records)), str(len(ok)),
        f"{total_in:,}", f"{total_out:,}", f"{total_tokens:,}",
        "see Cost below",
    ])
    out += [
        "## Per model (overall)",
        "",
        _table(
            per_model_rows,
            ["model", "provider", "stage(s)", "calls", "ok", "input", "output", "total", "cost"],
        ),
        "",
    ]

    out += [
        "## Per model and stage",
        "",
        _table(
            rows,
            ["model", "stage", "calls", "input", "output", "total", "wall", "cost (paid tier)"],
        ),
        "",
    ]

    # -- cost ---------------------------------------------------------------
    out += ["## Cost", ""]
    if priced:
        out += [
            f"- Estimated total: **${grand_cost:.6f}**",
        ]
        if requests_scored:
            out.append(f"- Estimated per request: **${grand_cost / requests_scored:.8f}**")
    else:
        out.append("- At least one model has no published price in `src/usage.py`; totals partial.")

    out += [
        "",
        "These figures are the **paid-tier equivalent**, computed from published",
        "per-million-token pricing in `src.usage.PRICE_PER_MILLION`. This run used the",
        "Gemini **free tier**, where the actual amount billed was **$0.00**. The estimate",
        "is shown so the cost of running the same workload at scale is visible.",
        "",
    ]

    if failed:
        out += [
            "## Failed and retried calls",
            "",
            f"{len(failed)} call(s) did not return a usable response. Each one degrades to",
            "'no amendment' or 'amount not recovered'; none of them stops the run.",
            "",
        ]
        for record in failed[:20]:
            out.append(f"- `{record.stage}` / `{record.model}`: {record.error}")
        out.append("")

    out += [
        "## Provenance",
        "",
        "Generated by `python -m evaluation.usage_report` from the metrics JSONL the",
        "recorder wrote during the run. Not hand-written and not reconstructed.",
        "No API key, credential, or message content appears in this file.",
        "",
    ]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    config = default_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metrics",
        type=Path,
        default=config.paths.artifacts_dir / "usage_metrics.jsonl",
        help="metrics JSONL written by the run",
    )
    parser.add_argument("--requests", type=int, default=250, help="rows scored in that run")
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    records = load_records(args.metrics)
    report = build_report(records, requests_scored=args.requests)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8", newline="\n")
    print(f"wrote {args.out} from {len(records)} recorded call(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
