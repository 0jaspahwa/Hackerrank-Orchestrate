"""B1: matched hold-out for the "fewest other resources" policy tie-break (D36 candidate rule).

The plain leak-free hold-out is biased: its truth policy actions carry identifiers, so their owners
own many resources. The real targets (anonymized analytics with no identifiers) belong to requests
that own little else. Matched hold-out = pinned policy actions whose true owner, apart from the
held-out resource, holds ONLY account + audit_record resources, OR only resources that are
compliant at audit_at. Each is held out leakage-free (pin removed, owner scrubbed from the owner map
and from the same user's other pins) and solved with policy_pick="fewest".
Run:  .venv/Scripts/python code/evaluation/tiebreak_matched.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.data import prefix_of                 # noqa: E402
from audit.pipeline import Auditor               # noqa: E402
from audit.signals import CoLocation             # noqa: E402

COMPLIANT = {"ABSENT", "CLEAN", "DISABLED", "BLOCKED", None}


def main():
    A = Auditor()
    s = A.solver
    pins = {c: set(v) for c, v in A.links.items()}
    full = s.solve(pins, user_of=A.user_of, owners=A.owners, signals=(CoLocation(A.ix, pins, A.R),))
    truth = [(k[0], a.activity_id, a.resource_id) for k, a in full.items()
             if s.actions[a.activity_id].kind == "policy" and a.how == "pinned"]
    owned = {}
    for k, a in full.items():
        owned.setdefault(k[0], set()).add(a.resource_id)
    groups = Counter()
    res = {"A: only account+audit": Counter(), "B: only compliant": Counter(), "all pinned (unmatched)": Counter()}
    for cid, aid, rid in truth:
        others = (pins[cid] | owned.get(cid, set())) - {rid}
        audit = A.R[cid]["audit_at"]
        only_base = {prefix_of(r) for r in others} <= {"account", "audit_record"}
        role = {r: (A.states.at(r, audit).state) for r in others if prefix_of(r) not in ("account", "audit_record")}
        only_compliant = all(v in COMPLIANT for v in role.values())
        p2 = {c: set(v) for c, v in pins.items()}
        for c2, v in p2.items():
            if A.user_of[c2] == A.user_of[cid]:
                v.discard(rid)
        ow = {r: set(u) for r, u in A.owners.items()}
        if rid in ow:
            ow[rid].discard(A.user_of[cid])
        s.policy_pick = "fewest"
        try:
            r = s.solve(p2, user_of=A.user_of, owners=ow, signals=(CoLocation(A.ix, p2, A.R),))
        finally:
            s.policy_pick = "omit"
        got = [(k[0], a.how) for k, a in r.items() if a.activity_id == aid]
        if not got:
            out = "abstain/unassigned"
        elif got[0][1] != "signal:fewest":
            out = ("correct" if got[0][0] == cid else "wrong") + "/" + got[0][1].split(":")[-1] + " (not the tie rule)"
        else:
            out = "correct" if got[0][0] == cid else "wrong"
        res["all pinned (unmatched)"][out] += 1
        if only_base:
            res["A: only account+audit"][out] += 1
        if only_compliant:
            res["B: only compliant"][out] += 1
    for name, c in res.items():
        decided = c["correct"] + c["wrong"]
        acc = c["correct"] / decided if decided else 0.0
        print(f"{name:26} n={sum(c.values()):3}  tie-rule correct {c['correct']}  wrong {c['wrong']}  "
              f"accuracy on its picks {acc:.2f}  other {dict((k, v) for k, v in c.items() if k not in ('correct', 'wrong'))}")


if __name__ == "__main__":
    main()
