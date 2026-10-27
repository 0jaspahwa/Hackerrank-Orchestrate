"""Compare the independent auditor's hand-derived rows with output.csv.

Run:  python code/evaluation/compare_audit.py [output.csv]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def core(s):
    return (s["phase"], s["operation"], s["system_id"], s["resource_id"])


def main(path=None, audit_dir="independent_audit", selection="audit_selection.json"):
    aud = json.loads((REPO / "code/evaluation" / audit_dir / "auditor_rows.json").read_text(encoding="utf-8"))
    out = {r["case_id"]: r for r in csv.DictReader(open(path or REPO / "output.csv", encoding="utf-8"))}
    sel = json.loads((REPO / "runs" / selection).read_text(encoding="utf-8"))
    stratum = {c: k for k, v in sel.items() for c in v}
    tot = {"status": 0, "exact": 0, "steps_a": 0, "steps_p": 0, "tp": 0, "ev_ok": 0}
    for cid in sorted(aud, key=lambda c: int(c.split("_")[1])):
        a = aud[cid]
        p = out[cid]
        ps = json.loads(p["steps_json"])
        a_core = {core(s): s for s in a["steps"]}
        p_core = {core(s): s for s in ps}
        both = set(a_core) & set(p_core)
        ev_diff = [k for k in both if a_core[k]["evidence_id"] != p_core[k]["evidence_id"]]
        exact = a["deletion_status"] == p["deletion_status"] and set(a_core) == set(p_core) and not ev_diff
        tot["status"] += a["deletion_status"] == p["deletion_status"]
        tot["exact"] += exact
        tot["steps_a"] += len(a_core)
        tot["steps_p"] += len(p_core)
        tot["tp"] += len(both)
        tot["ev_ok"] += len(both) - len(ev_diff)
        print(f"\n{cid} [{stratum.get(cid)}] auditor {a['deletion_status']} / pipeline {p['deletion_status']}"
              f" {'EXACT' if exact else ''}  steps auditor {len(a_core)} pipeline {len(p_core)} shared {len(both)}")
        for k in sorted(set(a_core) - set(p_core)):
            print(f"   auditor only : {k} ev={a_core[k]['evidence_id'] or '-'} conf={a_core[k].get('confidence')} :: {a_core[k].get('justification', '')[:150]}")
        for k in sorted(set(p_core) - set(a_core)):
            print(f"   pipeline only: {k} ev={p_core[k]['evidence_id'] or '-'}")
        for k in sorted(ev_diff):
            print(f"   evidence diff: {k} auditor={a_core[k]['evidence_id']} pipeline={p_core[k]['evidence_id']}")
    n = len(aud)
    print(f"\nstatus agreement {tot['status']}/{n}; exact rows {tot['exact']}/{n}; "
          f"shared steps {tot['tp']} (auditor {tot['steps_a']}, pipeline {tot['steps_p']}); "
          f"evidence agreement on shared {tot['ev_ok']}/{tot['tp']}")


if __name__ == "__main__":
    # python compare_audit.py [output.csv] [audit_dir] [selection.json]
    main(*sys.argv[1:4])
