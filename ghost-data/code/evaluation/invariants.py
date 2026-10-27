"""A2 global invariants over output.csv (+ traces). Run:  python code/evaluation/invariants.py [output.csv]"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DS = REPO / "dataset"
OMISSION_OK = "omission"     # absence proofs cite a store that, by definition, does not contain the id


def prefix(rid):
    return rid.rsplit("_", 1)[0]


def main(path=None):
    out = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
           for r in csv.DictReader(open(path or REPO / "output.csv", encoding="utf-8"))}
    req = {r["case_id"]: r for r in csv.DictReader(open(DS / "deletion_requests.csv", encoding="utf-8"))}
    man = {r["file_id"]: r for r in csv.DictReader(open(DS / "evidence_files.csv", encoding="utf-8"))}
    acts = {r["activity_id"]: r for r in csv.DictReader(open(DS / "system_activity.csv", encoding="utf-8"))}
    tr = {t["case_id"]: t for t in map(json.loads, open(REPO / "runs" / "trace.jsonl", encoding="utf-8"))}
    text_cache = {}

    def file_mentions(fid, rid):
        if fid not in text_cache:
            p = DS / man[fid]["file_path"]
            text_cache[fid] = "" if p.suffix in (".png", ".pdf") else p.read_text(encoding="utf-8", errors="ignore")
        return rid in text_cache[fid]

    v = defaultdict(list)
    owner = defaultdict(set)
    for cid, (st, steps) in out.items():
        uid = tr[cid]["user_id"]
        for s in steps:
            owner[s["resource_id"]].add((uid, cid))
    for rid, us in owner.items():
        if len({u for u, _ in us}) > 1:
            v["1 resource in two users' cases"].append((rid, sorted(us)))
    for cid, (st, steps) in out.items():
        if st == "NOT_APPLICABLE":
            continue
        rids = {s["resource_id"] for s in steps}
        if not any(s["system_id"] == "account_service" for s in steps):
            v["2 deletion case without account step"].append(cid)
        for p, n in Counter(prefix(r) for r in rids).items():
            if n > 1 and p != "support":
                v["3 >1 resource per prefix"].append((cid, p, sorted(r for r in rids if prefix(r) == p)))
        fin = {s["resource_id"] for s in steps if s["phase"] == "FINDING"}
        rem = {s["resource_id"] for s in steps if s["phase"] == "REMEDIATION"}
        ver = {s["resource_id"] for s in steps if s["phase"] == "VERIFICATION"}
        if fin != rem or not rem <= ver:
            v["4 finding/remediation/verification mismatch"].append((cid, sorted(fin ^ rem), sorted(rem - ver)))
        if st == "INCOMPLETE" and not fin:
            v["6 INCOMPLETE without finding"].append(cid)
        audit = req[cid]["audit_at"]
        for s in steps:
            ev = s["evidence_id"]
            if not ev:
                continue
            if ev.startswith("activity_"):
                if acts[ev]["event_time"] > audit or s["resource_id"] not in acts[ev]["details"] + acts[ev]["resource_id"]:
                    v["5 cited activity after audit_at or not about the resource"].append((cid, s["resource_id"], ev))
                continue
            if man[ev]["captured_at"] > audit:
                v["5 cited file captured after audit_at"].append((cid, s["resource_id"], ev))
            elif not file_mentions(ev, s["resource_id"]):
                res = tr[cid].get("resources", {}).get(s["resource_id"], {})
                kind = "artifact/screenshot or PDF" if (DS / man[ev]["file_path"]).suffix in (".png", ".pdf") else \
                    "omission proof" if res.get("state") is None and s["resource_id"].startswith("account_") else "UNEXPLAINED"
                v[f"5 cited file does not mention the resource ({kind})"].append((cid, s["resource_id"], ev))
    for k in sorted(v):
        print(f"{k}: {len(v[k])}")
        for x in v[k][:12]:
            print("    ", x)
    return v


if __name__ == "__main__":
    main(*sys.argv[1:2])
