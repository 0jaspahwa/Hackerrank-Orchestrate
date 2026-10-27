"""D29 measurement: deterministic pick vs omit for policy actions left with two candidates.

Hold-out: each identifier-pinned policy action has its own pin removed (the request keeps its
other links); the solve runs with policy_pick="omit" and "pick2".
  * how often the action ends unassigned with exactly 2 candidates,
  * how often the true owner is not among the remaining candidates,
  * accuracy of the deterministic pick (lowest natural case_id) on those.
Expected effect per pick event, relative to omit (p = pick accuracy, q = share of events whose
owner is outside the candidates, e.g. a non-requester):
  exact rows   correct pick +1 (owner row fixed), wrong pick -1 (owner still wrong or right,
               picked row broken) -> pick is better iff p > 1/2 (q counts as wrong)
  step F1      correct pick +1 TP, wrong pick +1 FP -> pick is better iff p > F1/2
Run:  python code/evaluation/policy_ties.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.pipeline import Auditor           # noqa: E402
from audit.signals import CoLocation         # noqa: E402
from audit.workflow import ACCOUNT_SLOT      # noqa: E402


def main():
    A = Auditor()
    s = A.solver
    pins = {c: set(v) for c, v in A.links.items()}
    sig = (CoLocation(A.ix, pins, A.R),)

    def solve(p, mode, owners=None):
        s.policy_pick = mode
        try:
            return s.solve(p, user_of=A.user_of, signals=sig,
                           owners=A.owners if owners is None else owners)
        finally:
            s.policy_pick = "omit"

    full = solve(pins, "omit")
    truth = [(k[0], a.activity_id, a.resource_id) for k, a in full.items()
             if s.actions[a.activity_id].kind == "policy" and a.how == "pinned"]
    c = Counter()
    for cid, aid, rid in truth:
        p2 = dict(pins)
        p2[cid] = pins[cid] - {rid}
        ow = dict(A.owners)                     # no leakage: the held-out resource loses its owner
        ow[rid] = ow.get(rid, set()) - {A.user_of[cid]}
        r = solve(p2, "omit", ow)
        got = [k[0] for k, a in r.items() if a.activity_id == aid]
        if got:
            c["omit: assigned " + ("correct" if got[0] == cid else "WRONG")] += 1
            continue
        cands = s.open_policy.get(aid, [])
        c[f"omit: unassigned, {len(cands)} candidates"] += 1
        r3 = solve(p2, "fewest", ow)
        got3 = [k[0] for k, a in r3.items() if a.activity_id == aid]
        c["fewest rule (any #cands): " + ("correct" if got3 and got3[0] == cid else "abstain" if not got3 else "wrong")] += 1
        if len(cands) == 2:
            c["2-cand: owner among candidates" if cid in cands else "2-cand: owner NOT among candidates"] += 1
            r2 = solve(p2, "pick2", ow)
            got2 = [k[0] for k, a in r2.items() if a.activity_id == aid]
            c["2-cand pick: " + ("correct" if got2 and got2[0] == cid else "wrong")] += 1
    for k in sorted(c):
        print(f"  {k:45} {c[k]}")
    picks = c["2-cand pick: correct"] + c["2-cand pick: wrong"]
    p = c["2-cand pick: correct"] / picks if picks else 0.0
    print(f"hold-out pick accuracy p = {c['2-cand pick: correct']}/{picks} = {p:.3f}")

    solve(pins, "omit")
    sizes = Counter(len(v) for v in s.open_policy.values())
    print("full solve, unresolved policy actions by remaining candidates:", dict(sorted(sizes.items())))
    fw = solve(pins, "fewest")
    print("fewest rule would assign", sum(1 for a in fw.values() if a.how == "signal:fewest"), "policy actions in the full solve")
    pk = solve(pins, "pick2")
    n_ties = sum(1 for a in pk.values() if a.how == "tie" and s.actions[a.activity_id].kind == "policy")
    print(f"pick2 would assign {n_ties} policy actions by tie in the full solve")
    print(f"expected exact-row change vs omit: {n_ties} x (2p-1) = {n_ties * (2 * p - 1):+.1f}")
    print(f"expected step TP / FP change vs omit: +{n_ties * p:.1f} TP / +{n_ties * (1 - p):.1f} FP")


if __name__ == "__main__":
    main()
