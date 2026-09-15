"""Route all 110 messages and write dataset/output.csv.

Cost is $0 by construction: media observation degrades without credits, voice
runs locally, and the text screen is deterministic. No model call is made.
"""

import collections
import csv
import sys
import time
from pathlib import Path

from branches import first_match
from main import IMAGE_DEPENDENT_BRANCHES, build_context, route_ctx
from writer import OUTPUT_PATH, write_output

DATASET = Path(__file__).resolve().parent.parent / "dataset"


def main() -> int:
    with open(DATASET / "messages.csv", encoding="utf-8") as fh:
        ids = [r["message_id"] for r in csv.DictReader(fh)]

    started = time.time()
    rows, branches, failures = [], collections.Counter(), []
    for mid in ids:
        try:
            ctx = build_context(mid)
            observation = ctx.get("observation") or {}
            degraded_image = observation.get("kind") == "image" and not observation.get("legible", True)
            fired = first_match(ctx)[0]
            # Mirror route_ctx's dispatch exactly, or this counter lies.
            branches[
                "route_degraded_image"
                if degraded_image and fired in IMAGE_DEPENDENT_BRANCHES
                else fired
            ] += 1
            rows.append(route_ctx(ctx))
        except Exception as exc:  # noqa: BLE001 - a crash here fails all 110
            failures.append((mid, f"{type(exc).__name__}: {exc}"))
    elapsed = time.time() - started

    written = write_output(rows)

    print(f"rows routed      : {len(rows)}/{len(ids)}")
    print(f"rows written     : {written}")
    print(f"exceptions       : {len(failures)}")
    for mid, err in failures:
        print(f"    {mid}: {err}")
    print(f"elapsed          : {elapsed:.1f}s")
    print("API cost         : $0.00 (no model calls - vision degraded, ASR local, screen deterministic)")

    print("\naction distribution")
    for k, v in collections.Counter(str(r.action) for r in rows).most_common():
        print(f"  {k:10} {v:3}  {v / len(rows):5.1%}")
    print("\nmessage_type distribution")
    for k, v in collections.Counter(str(r.message_type) for r in rows).most_common():
        print(f"  {k:18} {v:3}  {v / len(rows):5.1%}")

    print("\nper-branch fire count")
    from branches import ORDERED_BRANCHES
    names = [b[0] for b in ORDERED_BRANCHES] + ["route_degraded_image"]
    for name in names:
        n = branches.get(name, 0)
        tag = "   <- DEAD" if n == 0 else ""
        print(f"  {name:24} {n:3}{tag}")
    print(f"\nfell through     : {branches.get('fall-through', 0)}")

    print("\nevidence")
    none = sum(1 for r in rows if not r.evidence_message_ids)
    print(f"  rows citing evidence : {len(rows) - none}")
    print(f"  rows citing 'none'   : {none}")
    return 0 if not failures and written == len(ids) else 1


if __name__ == "__main__":
    sys.exit(main())
