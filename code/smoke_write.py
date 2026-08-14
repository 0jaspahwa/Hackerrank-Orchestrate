"""Smoke run: write all 110 rows as safe defaults and verify the file contract.

Proves the I/O path end to end while there is still no decision layer to blame
for a failure. Every row is the Phase 1 A7 safe default.

References, chosen so the check can never become circular:
  header  - derived from schema.COLUMNS, the single source of truth
  ids     - dataset/messages.csv, an input this script never writes
  spec    - problem_statement.md, the contract as written by the organizers

Deliberately NOT compared against dataset/output.csv, which this script
overwrites - that would validate our output against our own previous output.

Check (c) alone is self-referential: writer.py writes its header from COLUMNS
and (c) expects COLUMNS, so a wrong COLUMNS would satisfy both. Check (d) is
what actually pins COLUMNS to the contract.
"""

import csv
import re
import sys
from pathlib import Path

from schema import COLUMNS, Action, MessageType, OutputRow
from writer import OUTPUT_PATH, write_output

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "dataset"
SPEC_PATH = REPO / "problem_statement.md"

EXPECTED_HEADER = ",".join(COLUMNS).encode("utf-8")

# Bullets must be strictly consecutive lines: the block is followed by a blank
# line and then "## Output meaning", whose bullets are also backticked and must
# not be swept in.
_COLUMN_BLOCK = re.compile(r"Required columns, in order:\n\n((?:- `[^`]+`\n)+)")


def spec_header() -> str:
    """The header string assembled from problem_statement.md itself."""
    block = _COLUMN_BLOCK.search(SPEC_PATH.read_text(encoding="utf-8"))
    if block is None:
        raise ValueError("could not locate the 'Required columns, in order:' block")
    return ",".join(re.findall(r"`([^`]+)`", block.group(1)))


def main() -> int:
    with open(DATASET / "messages.csv", encoding="utf-8") as fh:
        message_ids = [r["message_id"] for r in csv.DictReader(fh)]

    rows = [
        OutputRow(
            message_id=mid,
            action=Action.DIGEST,
            message_type=MessageType.UNKNOWN,
            reason="fallback, no decision layer yet",
            confidence=0.5,
            evidence_message_ids=[],
        )
        for mid in message_ids
    ]
    written = write_output(rows)

    raw = OUTPUT_PATH.read_bytes()
    lines = raw.split(b"\r\n")
    header = lines[0]
    ids = [ln.split(b",")[0].decode() for ln in lines[1:] if ln]

    checks = [
        ("(a) row count matches input", len(ids) == len(message_ids), f"{len(ids)} vs {len(message_ids)}"),
        ("(b) ids in input order", ids == message_ids, "exact sequence match"),
        ("(c) header bytes == COLUMNS", header == EXPECTED_HEADER, header.decode()),
        ("(d) COLUMNS == problem_statement", spec_header() == ",".join(COLUMNS), spec_header()),
        ("    rows written == 110", written == 110, str(written)),
        ("    CRLF only, no bare LF", raw.count(b"\n") == raw.count(b"\r\n"), f"{raw.count(b'\r\n')} CRLF"),
        ("    no BOM", not raw.startswith(b"\xef\xbb\xbf"), ""),
        ("    trailing newline", raw.endswith(b"\r\n"), ""),
        ("    confidence is 2dp", all(ln.split(b",")[-2] == b"0.50" for ln in lines[1:] if ln), "0.50"),
        ("    evidence is 'none'", all(ln.split(b",")[-1] == b"none" for ln in lines[1:] if ln), "none"),
    ]

    ok = True
    for label, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label:34} {detail}")
        ok &= passed

    print("\nfirst 2 data rows:")
    for ln in lines[1:3]:
        print("   ", ln.decode())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
