"""D43 2-fold cross-validation of the account timing signature.

Truth: identifier-pinned accounts (pipeline pins). For each seed, split them in half; learn the
residue set ((first own follow-up - account submission) mod 60 s) on fold A only, then measure the
leakage-free leave-one-out accuracy of the adopted solve + signature on every fold-B request; swap.
Run:  python code/evaluation/signature_cv.py [seed ...]
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.pipeline import Auditor                          # noqa: E402
from audit.signals import CoLocation, FollowupSignature     # noqa: E402
from audit.workflow import ACCOUNT_SLOT, _epoch             # noqa: E402


def residue(A, cid, acc_t, rows):
    mine = sorted(t for r in A.links[cid] if not r.startswith("account_") for t in rows.get(r, ()))
    nxt = [m for m in mine if 0 < m - acc_t <= FollowupSignature.WINDOW]
    return round(nxt[0] - acc_t) % 60 if nxt else None


def main(seeds):
    A = Auditor()
    s = A.solver
    pins = {c: set(v) for c, v in A.links.items()}
    full = s.solve(pins, user_of=A.user_of, owners=A.owners, signals=(CoLocation(A.ix, pins, A.R),), break_ties=False)
    truth = {k[0]: a for k, a in full.items() if k[1] == ACCOUNT_SLOT and a.how == "pinned"}
    rows = {}
    for a in A.ds.activity:
        if a["source_system"] == "deletion_worker" and not a["resource_id"].startswith("account_"):
            rows.setdefault(a["resource_id"], []).append(_epoch(a["event_time"]))
    res_of = {c: residue(A, c, s.actions[a.activity_id].t, rows) for c, a in truth.items()}
    print(f"truth accounts: {len(truth)}; with a follow-up residue: {sum(r is not None for r in res_of.values())}")
    print("residue distribution (all):", dict(sorted(Counter(r for r in res_of.values() if r is not None).items())))

    def loo(test, allowed):
        c = Counter()
        for cid in test:
            rid = truth[cid].resource_id
            p = {x: set(v) for x, v in pins.items()}
            for x, v in p.items():
                if A.user_of[x] == A.user_of[cid]:
                    v.discard(rid)
            ow = {r: set(u) for r, u in A.owners.items()}
            if rid in ow:
                ow[rid].discard(A.user_of[cid])
            sig = (FollowupSignature(A.ds.activity, p, allowed), CoLocation(A.ix, p, A.R))
            r = s.solve(p, user_of=A.user_of, owners=ow, signals=sig, hide={cid})
            a = r.get((cid, ACCOUNT_SLOT))
            c["unassigned" if a is None else ("correct" if a.resource_id == rid else "wrong") + "/" + a.how.split(":")[-1]] += 1
        return c

    ok = True
    for seed in seeds:
        ids = sorted(truth, key=lambda c: int(c.split("_")[1]))
        random.Random(seed).shuffle(ids)
        half = len(ids) // 2
        folds = {"A": ids[:half], "B": ids[half:]}
        for train, test in (("A", "B"), ("B", "A")):
            allowed = {res_of[c] for c in folds[train] if res_of[c] is not None}
            c = loo(folds[test], allowed)
            n = sum(c.values())
            acc = sum(v for k, v in c.items() if k.startswith("correct")) / n
            ok &= acc >= 0.95
            print(f"seed {seed}: learn on {train} ({len(folds[train])}) residues {sorted(allowed)} -> "
                  f"LOO on {test} ({n}): {acc:.3f}  {dict(sorted(c.items()))}")
    print("ADOPTION CRITERION (every fold >= 0.95):", "MET" if ok else "NOT MET")


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or [20261025, 7])
