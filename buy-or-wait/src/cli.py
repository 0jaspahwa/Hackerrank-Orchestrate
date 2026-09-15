"""Terminal entry point (AGENTS.md 6.4: must be runnable from the terminal).

    python -m src.cli
    python -m src.cli --requests dataset/requests.csv --out output.csv

Argument parsing only; the work lives in `run`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from src.config import Config, Paths, default_config
from src.run import run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buy or Wait? -- decide every request.")
    parser.add_argument("--dataset", type=Path, default=None, help="dataset directory")
    parser.add_argument("--requests", type=Path, default=None, help="requests CSV to score")
    parser.add_argument("--out", type=Path, default=None, help="output CSV path")
    parser.add_argument("--trace", type=Path, default=None, help="JSONL trace path")
    parser.add_argument(
        "--use-model",
        action="store_true",
        help="enable the OCR and message layers (needs GEMINI_API_KEY)",
    )
    parser.add_argument("--metrics", type=Path, default=None, help="usage metrics JSONL path")
    args = parser.parse_args(argv)

    config: Config = default_config()
    if args.dataset or args.out:
        config = config.model_copy(
            update={
                "paths": Paths(
                    dataset_dir=args.dataset or config.paths.dataset_dir,
                    output_csv=args.out or config.paths.output_csv,
                    images_dir=(args.dataset or config.paths.dataset_dir) / "media" / "images",
                )
            }
        )

    rows, summary = run(
        config,
        requests_path=args.requests,
        output_path=args.out,
        trace_path=args.trace,
        use_model=args.use_model,
        metrics_path=args.metrics,
    )

    print(f"wrote {summary.rows} rows to {args.out or config.paths.output_csv}")
    print(f"  status: {summary.status_counts}")
    if args.use_model:
        print(
            f"  model:  enabled={summary.model_enabled} calls={summary.model_calls} "
            f"in={summary.input_tokens} out={summary.output_tokens} "
            f"ocr_ok={summary.ocr_extracted} ocr_failed={summary.ocr_failed} "
            f"amendments={summary.amendments_applied}"
        )
    print(f"  method: {summary.method_counts}")
    if summary.fallbacks:
        print(f"  SAFE_DEFAULT fallbacks: {len(summary.fallbacks)}", file=sys.stderr)
        for line in summary.fallbacks:
            print(f"    {line}", file=sys.stderr)
    for note in summary.loud_notes:
        print(f"  !! {note}", file=sys.stderr)
    _ = rows
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
