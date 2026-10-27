"""Diff two output.csv files (and their traces): which cases changed status or steps, and why.

Run:  python code/evaluation/diff_outputs.py runs/output_prev_rules_only.csv output.csv \
          runs/trace_prev_rules_only.jsonl runs/trace.jsonl
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path


def rows(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def traces(p):
    return {t["case_id"]: t for t in map(json.loads, open(p, encoding="utf-8"))}


def key(s):
    return (s["phase"], s["operation"], s["system_id"], s["resource_id"], s["evidence_id"])


def main(a, b, ta, tb):
    A, B, TA, TB = rows(a), rows(b), traces(ta), traces(tb)
    status = Counter()
    changed = []
    for cid in sorted(A, key=lambda c: int(c.split("_")[1])):
        (sa, xa), (sb, xb) = A[cid], B[cid]
        if sa == sb and xa == xb:
            continue
        ka, kb = Counter(map(key, xa)), Counter(map(key, xb))
        why = []
        ra, rb = TA[cid].get("resources", {}), TB[cid].get("resources", {})
        for rid in sorted(set(ra) | set(rb)):
            x, y = ra.get(rid), rb.get(rid)
            if x is None:
                why.append(f"+{rid} ({'; '.join(y['linked_by'][:2])}; state {y['state']})")
            elif y is None:
                why.append(f"-{rid}")
            elif (x["state"], x["cite"], x["category"]) != (y["state"], y["cite"], y["category"]):
                why.append(f"{rid}: state {x['state']}->{y['state']}, cite {x['cite'] or '-'}->{y['cite'] or '-'}"
                           + (f", category {x['category']}->{y['category']}" if x["category"] != y["category"] else ""))
        if TA[cid]["intent"]["deletion"] != TB[cid]["intent"]["deletion"]:
            why.append(f"intent {TA[cid]['intent']['deletion']}->{TB[cid]['intent']['deletion']}")
        status[(sa, sb)] += 1
        changed.append((cid, sa, sb, sorted(ka - kb), sorted(kb - ka), why))
    print(f"cases changed: {len(changed)} of {len(A)}")
    print("status transitions:", {f"{k[0]}->{k[1]}": v for k, v in sorted(status.items())})
    for cid, sa, sb, rem, add, why in changed:
        print(f"\n{cid}: {sa} -> {sb}")
        for s in rem:
            print(f"   - {s}")
        for s in add:
            print(f"   + {s}")
        for w in why:
            print(f"   why: {w}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
