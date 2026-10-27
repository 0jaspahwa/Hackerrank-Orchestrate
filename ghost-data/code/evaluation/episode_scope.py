"""Q8 measurement: how many eval (request, resource) links each episode rule keeps or drops.

  A (Phase-1 rule)  drop when every discoverable link predates requested_at
  B (DECISIONS D16) drop only when, additionally, the user has prior-episode evidence:
                    an earlier request in the dataset, an earlier deletion-worker action on one
                    of the user's resources, or an earlier tombstone / catalog LINK for the user.
Run:  python code/evaluation/episode_scope.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.data import Dataset              # noqa: E402
from timing_holdout import build            # noqa: E402


def main():
    ds = Dataset.load()
    ix, idr, lk, reqs, pins, user_of = build(ds)
    by_user = defaultdict(list)
    for r in reqs:
        by_user[user_of[r["case_id"]]].append(r)
    dw = defaultdict(list)
    for a in ds.activity:
        if a["source_system"] == "deletion_worker":
            dw[a["resource_id"]].append(a["event_time"])
    cnt, dropped = Counter(), []
    for r in ds.requests:
        s = idr.resolve(r["email"])
        links = lk.linked_resource_times(s)
        prior_req = [x["case_id"] for x in by_user[s.user_id] if x["audit_at"] < r["requested_at"]]
        prior_dw = any(t < r["requested_at"] for rid in links for t in dw.get(rid, []))
        prior_link = any(c["time"] < r["requested_at"] for c in lk.catalog_links(s))
        prior = bool(prior_req or prior_dw or prior_link)
        for rid, v in links.items():
            disc = [e for e, _, _ in v if e <= r["audit_at"]]
            if not disc:
                continue
            cnt["linked"] += 1
            early = all(e < r["requested_at"] for e in disc)
            cnt["A_drop" if early else "A_keep"] += 1
            cnt["B_drop" if early and prior else "B_keep"] += 1
            if early:
                dropped.append((r["case_id"], rid, "B drops" if prior else "A only",
                                prior_req or ("deletion-worker" if prior_dw else "link" if prior_link else "-")))
    print(dict(cnt))
    for d in dropped:
        print(" ", d)


if __name__ == "__main__":
    main()
