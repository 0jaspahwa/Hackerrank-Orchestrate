"""All timing hold-outs, leakage-free, with every adopted signal ablated separately.

Protocol (same pins and owners as the pipeline; filled slots were removed, D34):
  * a hidden request loses all its identifier pins (solver `hide`); its user is removed from the
    resource-owner map (D26) and the truth resource is removed from the pins of every other request
    of the same user, so no identity of the true owner reaches the solver;
  * co-location only sees the hidden request's own *non-account* links (what the real pipeline sees
    for a request without an identifier-linked account);
  * policy actions: only the held-out action's pin is removed, plus the same owner scrub for it.
Run:  python code/evaluation/holdout_all.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.pipeline import Auditor           # noqa: E402
from audit.signals import CoLocation, FollowupSignature   # noqa: E402
from audit.workflow import ACCOUNT_SLOT, WorkflowSolver   # noqa: E402

LEGACY = ("legacy", "DELETE_RECORD", "account_service")


def main():
    A = Auditor()
    s: WorkflowSolver = A.solver
    pins = {c: set(v) for c, v in A.links.items()}
    full = s.solve(pins, user_of=A.user_of, owners=A.owners, break_ties=False)
    acc_truth = {k[0]: a.resource_id for k, a in full.items() if k[1] == ACCOUNT_SLOT and a.how == "pinned"}
    pol_truth = [(k[0], a.activity_id, a.resource_id) for k, a in full.items()
                 if s.actions[a.activity_id].kind == "policy" and a.how == "pinned"]

    def scrub(hidden: dict):
        """hidden: request -> truth resource. Returns leak-free (pins, owners)."""
        p = {c: set(v) for c, v in pins.items()}
        ow = {r: set(u) for r, u in A.owners.items()}
        for cid, rid in hidden.items():
            u = A.user_of[cid]
            for c2, v in p.items():
                if A.user_of[c2] == u:
                    v.discard(rid)
            if rid in ow:
                ow[rid].discard(u)
        return p, ow

    def run(hidden, hide_requests, cfg):
        p, ow = scrub(hidden)
        pv = {c: (v - set(hidden.values())) for c, v in p.items()}
        sig = (CoLocation(A.ix, pv, A.R),) if cfg.get("coloc") else ()
        if cfg.get("sig"):
            sig = (FollowupSignature(A.ds.activity, pv),) + sig
        return s.solve(p, user_of=A.user_of if cfg.get("foreign") else None, hide=hide_requests, signals=sig,
                       owners=ow if cfg.get("owners") else None)

    def grade(a, rid):
        if a is None:
            return "unassigned"
        return ("correct" if a.resource_id == rid else "wrong") + "/" + a.how.split(":")[0]

    configs = [("timing only", {}), ("+foreign (requesters)", {"foreign": 1}), ("+owners (all users)", {"owners": 1}),
               ("+colocation", {"coloc": 1}),
               ("ADOPTED (foreign+owners+coloc)", {"foreign": 1, "owners": 1, "coloc": 1}),
               ("+signature only", {"sig": 1}),
               ("ADOPTED + signature", {"foreign": 1, "owners": 1, "coloc": 1, "sig": 1})]
    print(f"account hold-out: {len(acc_truth)} requests with an identifier-pinned account")
    base = {}
    for name, cfg in configs:
        loo = Counter()
        for cid, rid in acc_truth.items():
            r = run({cid: rid}, {cid}, cfg)
            loo[grade(r.get((cid, ACCOUNT_SLOT)), rid)] += 1
        r = run(acc_truth, set(acc_truth), cfg)
        ha = Counter(grade(r.get((cid, ACCOUNT_SLOT)), rid) for cid, rid in acc_truth.items())
        res = []
        for mode, c in (("leave-one-out", loo), ("hide-all", ha)):
            n = sum(c.values())
            ok = sum(v for k, v in c.items() if k.startswith("correct"))
            res.append(ok / n)
            print(f"  [{name:24}] {mode:13} {ok}/{n} = {ok / n:.3f}  {dict(sorted(c.items()))}")
        base[name] = res
    for name in list(base)[1:]:
        d = [round(base[name][i] - base["timing only"][i], 3) for i in (0, 1)]
        print(f"  delta vs timing only  {name:24} LOO {d[0]:+.3f}  hide-all {d[1]:+.3f}"
              + ("   <-- does NOT beat baseline" if max(d) <= 0 and name != "ADOPTED (all four)" else ""))

    print(f"\npolicy hold-out: {len(pol_truth)} identifier-pinned policy actions")
    pol_cfgs = [("unique only", dict(use_chain=False, use_prefix=False, use_tombstone=False)),
                ("+chain (all)", dict(use_chain=True, use_prefix=False, use_tombstone=False, chain_mode="all")),
                ("+chain (adjacent)", dict(use_chain=True, use_prefix=False, use_tombstone=False, chain_mode="adjacent")),
                ("+prefix", dict(use_chain=False, use_prefix=True, use_tombstone=False)),
                ("+tombstone", dict(use_chain=False, use_prefix=False, use_tombstone=True)),
                ("chain(adj)+prefix+tomb", dict(use_chain=True, use_prefix=True, use_tombstone=True, chain_mode="adjacent", use_policy_signature=False)),
                ("+ policy signature (ADOPTED)", dict(use_chain=True, use_prefix=True, use_tombstone=True, chain_mode="adjacent", use_policy_signature=True)),
                ("signature only (no chain)", dict(use_chain=False, use_prefix=True, use_tombstone=True, use_policy_signature=True))]
    for name, sw in pol_cfgs:
        for k, v in sw.items():
            setattr(s, k, v)
        c = Counter()
        for cid, aid, rid in pol_truth:
            p, ow = scrub({cid: rid})
            r = s.solve(p, user_of=A.user_of, owners=ow,
                        signals=(FollowupSignature(A.ds.activity, p), CoLocation(A.ix, p, A.R)))
            got = [(k[0], a.how) for k, a in r.items() if a.activity_id == aid]
            c["unassigned" if not got else ("correct/" if got[0][0] == cid else "WRONG/") + got[0][1].split(":")[-1]] += 1
        for k in ("use_chain", "use_prefix", "use_tombstone", "use_policy_signature"):
            setattr(s, k, True)
        s.chain_mode = "adjacent"
        ok = sum(v for k, v in c.items() if k.startswith("correct"))
        wrong = sum(v for k, v in c.items() if k.startswith("WRONG"))
        print(f"  [{name:20}] correct {ok}/{len(pol_truth)}  wrong {wrong}  {dict(sorted(c.items()))}")


if __name__ == "__main__":
    main()
