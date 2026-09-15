"""Score the pipeline against the 30 labeled samples, and map every error.

output.csv cannot be scored against sample_messages.csv - the id sets are
disjoint (msg_* vs sample_msg_*). This runs the same route path over the
labeled rows instead.

Errors are attributed to a layer, not just counted:
  L1 joins/retrieval   L2 observation flag   L3 branch condition
  L4 branch-to-output mapping
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

from branches import first_match  # noqa: E402
from check_branches import build_ctx, load  # noqa: E402
from main import attach_observations, route_ctx  # noqa: E402


def main() -> int:
    samples = load("sample_messages")
    results = []
    for row in samples:
        ctx = attach_observations(build_ctx(row))
        hit = first_match(ctx)
        observation = ctx.get("observation") or {}
        degraded = observation.get("kind") == "image" and not observation.get("legible", True)
        out = route_ctx(ctx)
        results.append({
            "id": row["message_id"],
            "branch": "route_degraded_image" if degraded else hit[0],
            "pred": f"{out.action}/{out.message_type}",
            "truth": f"{row['action']}/{row['message_type']}",
            "conf": out.confidence,
            "row": row,
            "ctx": ctx,
        })

    act = sum(r["pred"].split("/")[0] == r["truth"].split("/")[0] for r in results)
    both = sum(r["pred"] == r["truth"] for r in results)
    print(f"action-correct on the 30 : {act}/30 = {act/30:.0%}")
    print(f"both-correct on the 30   : {both}/30 = {both/30:.0%}")

    wrong = [r for r in results if r["pred"] != r["truth"]]
    print(f"wrong rows               : {len(wrong)}\n")
    print(f"{'id':16}{'predicted':26}{'truth':26}{'branch':24}conf")
    print("-" * 100)
    for r in wrong:
        print(f"{r['id']:16}{r['pred']:26}{r['truth']:26}{r['branch']:24}{r['conf']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
