"""Write predictions to output.csv in the exact submission format.

Format facts measured from the shipped dataset/output.csv:
  no BOM, CRLF line endings throughout, trailing newline, header is
  message_id,action,message_type,reason,confidence,evidence_message_ids

The ';'-join and the 'none' sentinel are NOT implemented here - they come from
the field_serializer on OutputRow. One rule, one place.
"""

import csv
from collections.abc import Iterable
from pathlib import Path

from schema import COLUMNS, OutputRow

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "dataset" / "output.csv"


def write_output(rows: Iterable[OutputRow], path: Path = OUTPUT_PATH) -> int:
    """Write rows to path. Returns the number of data rows written.

    newline="" is required: without it Python's text layer also translates \\n,
    and csv's \\r\\n terminator becomes \\r\\r\\n on Windows.
    """
    written = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            cells = row.model_dump(mode="json")
            cells["confidence"] = f"{row.confidence:.2f}"
            writer.writerow(cells)
            written += 1
    return written
