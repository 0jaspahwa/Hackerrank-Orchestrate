"""Hold-out measurement of the workflow timing assignment (DECISIONS D15).

Truth = resources identifier-linked to a request (pins). For each, hide the link and
check what the timing solve assigns.
  * accounts, leave-one-out: hide one request's pins at a time (neighbours stay pinned)
  * accounts, hide-all: hide every hold-out request at once (pessimistic lower bound)
  * policy actions: remove just that one pin (the request keeps its other anchors)
Run:  python code/evaluation/timing_holdout.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.data import Dataset                          # noqa: E402
from audit.evidence import EvidenceIndex                # noqa: E402
from audit.identity import IdentityResolver             # noqa: E402
from audit.linking import Linker                        # noqa: E402
from audit.signals import CoLocation                    # noqa: E402
from audit.workflow import ACCOUNT_SLOT, WorkflowSolver  # noqa: E402


def build(ds):
    ix = EvidenceIndex(ds)
    idr = IdentityResolver(ds)
    lk = Linker(ds, ix, idr)
    reqs = ds.samples + ds.requests
    pins = {r["case_id"]: set(lk.linked_resource_times(idr.resolve(r["email"]))) for r in reqs}
    user_of = {r["case_id"]: idr.resolve(r["email"]).user_id for r in reqs}
    return ix, idr, lk, reqs, pins, user_of


def grade(a, rid):
    if a is None:
        return "unassigned"
    return ("correct" if a.resource_id == rid else "wrong") + "/" + a.how.split(":")[0]


def account_stats(solver, pins, user_of, signals):
    res = solver.solve(pins, user_of=user_of, signals=signals, break_ties=False)
    any_cand = {c for wa in solver.actions.values() if wa.kind == "account" for c in wa.candidates}
    out = {}
    for cid in solver.requests:
        a = res.get((cid, ACCOUNT_SLOT))
        if a is not None:
            out[cid] = a.how.split(":")[0]
        elif len(solver.open_accounts.get((cid, ACCOUNT_SLOT), ())) > 1:
            out[cid] = "ambiguous"
        else:
            out[cid] = "infeasible" if cid in any_cand else "no-event"
    return out, res


def main():
    ds = Dataset.load()
    ix, idr, lk, reqs, pins, user_of = build(ds)
    solver = WorkflowSolver(reqs, ds.activity)
    R = {r["case_id"]: r for r in reqs}
    sample_ids = {r["case_id"] for r in ds.samples}
    coloc = CoLocation(ix, pins, R)

    for name, sigs in (("timing+foreign", ()), ("timing+foreign+colocation", (coloc,))):
        st, _ = account_stats(solver, pins, user_of, sigs)
        print(f"[{name}] account slot, all 550:", dict(Counter(st.values())))
        print(f"[{name}] account slot, eval 518:", dict(Counter(v for k, v in st.items() if k not in sample_ids)))

    full = solver.solve(pins, user_of=user_of, break_ties=False)
    truth = {k[0]: a.resource_id for k, a in full.items() if k[1] == ACCOUNT_SLOT and a.how == "pinned"}
    print("\naccount hold-out size (identifier-linked accounts):", len(truth))
    # resources carrying identifiers of users who filed no request (no leakage: requesters are excluded)
    from audit.pipeline import Auditor
    requesters = set(user_of.values())
    owners = {k: v - requesters for k, v in Auditor(ds)._resource_owners().items() if v - requesters}
    configs = [("timing only", (), None, None), ("timing+foreign", (), user_of, None),
               ("timing+foreign+colocation", (coloc,), user_of, None),
               ("+non-requester owners", (coloc,), user_of, owners)]
    for name, sigs, uo, ow in configs:
        loo = Counter()
        for cid, rid in truth.items():
            r = solver.solve(pins, user_of=uo, signals=sigs, hide={cid}, owners=ow)
            loo[grade(r.get((cid, ACCOUNT_SLOT)), rid)] += 1
        r = solver.solve(pins, user_of=uo, signals=sigs, hide=set(truth), owners=ow)
        ha = Counter(grade(r.get((cid, ACCOUNT_SLOT)), rid) for cid, rid in truth.items())
        for mode, c in (("leave-one-out", loo), ("hide-all", ha)):
            n = sum(c.values())
            ok = sum(v for k, v in c.items() if k.startswith("correct"))
            print(f"  [{name:26}] {mode:13} {ok}/{n} = {ok / n:.3f}  {dict(sorted(c.items()))}")

    ptruth = [(k[0], a.activity_id, a.resource_id) for k, a in full.items()
              if solver.actions[a.activity_id].kind == "policy" and a.how == "pinned"]
    pc = Counter()
    for cid, aid, rid in ptruth:
        p2 = dict(pins)
        p2[cid] = pins[cid] - {rid}
        r = solver.solve(p2, user_of=user_of)
        got = [k[0] for k, a in r.items() if a.activity_id == aid]
        how = [a.how.split(":")[-1] for k, a in r.items() if a.activity_id == aid]
        pc["unassigned" if not got else ("correct/" if got[0] == cid else "wrong/") + how[0]] += 1
    n = len(ptruth)
    ok = sum(v for k, v in pc.items() if k.startswith("correct"))
    print(f"\npolicy-action hold-out: {ok}/{n} correct, {dict(sorted(pc.items()))}")
    res = solver.solve(pins, user_of=user_of, signals=(coloc,))
    pol = Counter(a.how.split(':')[-1] for a in res.values() if solver.actions[a.activity_id].kind == "policy")
    print("policy actions, full solve:", dict(pol), "unresolved:", len(solver.open_policy))


if __name__ == "__main__":
    main()
