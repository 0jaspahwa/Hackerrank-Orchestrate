"""A1 recall audit: every (case, resource) linked to the requester that produces no step, with a reason.

Linked = identifier link in any evidence record, catalog LINK row, or source_resource_id (any time).
Reason codes: post-audit only | prior episode (D16) | D24 prefix conflict | unscoped prefix |
owned by another user | other.
Run (repo venv, cache only):  .venv/Scripts/python code/evaluation/recall_audit.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.data import REPO_ROOT, Dataset, prefix_of     # noqa: E402
from audit.pipeline import Auditor                       # noqa: E402
from audit.policy import UNSCOPED                        # noqa: E402


def build(offline=True):
    ds = Dataset.load()
    from llm.vision import extract_all
    images = extract_all(ds, log_name="vision_dropped_recall.jsonl")
    return Auditor(ds, image_extractions=images)


def main():
    A = build()
    eval_ids = [r["case_id"] for r in A.ds.requests]
    rows, codes = [], Counter()
    for cid in eval_ids:
        res = A.audit(cid)
        if res["deletion_status"] == "NOT_APPLICABLE":
            continue
        t = res["trace"]
        stepped = {s["resource_id"] for s in res["steps"]}
        links = A.lk.linked_resource_times(A.subject[cid])
        audit = A.R[cid]["audit_at"]
        for rid, v in sorted(links.items()):
            if rid in stepped:
                continue
            others = A.owners.get(rid, set()) - {A.user_of[cid]}
            if prefix_of(rid) in UNSCOPED:
                code = "unscoped prefix"
            elif all(e > audit for e, _, _ in v):
                code = "post-audit only"
            elif rid in t.get("excluded_prior_episode", {}):
                code = "prior episode (D16)"
            elif rid in t.get("excluded_weak_link", {}):
                code = "D24 prefix conflict"
            elif others and A.user_of[cid] not in A.owners.get(rid, set()):
                code = "owned by another user"
            else:
                code = "other"
            codes[code] += 1
            rows.append({"case_id": cid, "resource_id": rid, "code": code,
                         "links": sorted({(e, src) for e, _, src in v})[:3],
                         "other_owners": sorted(others)})
    out = REPO_ROOT / "runs" / "recall_audit.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print("unstepped linked (case, resource) pairs by reason:", dict(codes))
    for r in rows:
        if r["code"] == "other":
            print("  OTHER", r)


if __name__ == "__main__":
    main()
