"""Contract check of ./output.csv against dataset/deletion_requests.csv (no gold needed).

Run:  python code/evaluation/validate_output.py [path]
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grade import REPO, validate        # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else REPO / "output.csv"
    with open(REPO / "dataset" / "deletion_requests.csv", encoding="utf-8", newline="") as fh:
        expected = [r["case_id"] for r in csv.DictReader(fh)]
    errors = validate(path, expected)
    print(f"{path}: {len(expected)} expected rows, {len(errors)} contract errors")
    for e in errors[:50]:
        print("  -", e)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
