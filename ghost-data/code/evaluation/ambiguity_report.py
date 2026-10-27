"""Risk of the deterministic account tie-break (D15): what can a wrong pick change?

For every request whose account slot is still ambiguous after propagation and signals, look at
each candidate account's state at *that* request's audit_at:
  status-flip risk   candidates differ in compliance (one PRESENT, another ABSENT/unobserved),
                     so a wrong pick could add or remove a finding and flip deletion_status
  evidence risk      all compliant, but the citation a COMPLETE case would carry differs
  resource-id only   all compliant with the same citation: a wrong pick changes one step's
                     resource_id and nothing else
Also reports whether any candidate is observed in a file captured within 24 h before that
request's audit_at with an identifier of the subject (it would have been linked already) or of
another user (excluded already), i.e. whether evidence near audit_at could narrow the pick.
Run:  python code/evaluation/ambiguity_report.py
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.pipeline import Auditor           # noqa: E402

COMPLIANT = {None, "ABSENT"}


def main():
    A = Auditor()
    eval_ids = {r["case_id"] for r in A.ds.requests}
    out = Counter()
    rows = []
    for (cid, _slot), dom in sorted(A.open_accounts.items()):
        audit = A.R[cid]["audit_at"]
        near = (datetime.fromisoformat(audit.replace("Z", "+00:00")) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cands = []
        for aid in sorted(dom):
            rid = A.solver.actions[aid].resource_id
            st = A.states.at(rid, audit)
            cite = st.cite or A.states.omission_proof(rid, audit)
            near_obs = [o for o in A.ix.by_rid.get(rid, ()) if near <= o.captured_at <= audit]
            cands.append((rid, st.state, cite, len(near_obs), bool(A.owners.get(rid))))
        states = {c[1] for c in cands}
        if not states <= COMPLIANT and len(states) > 1:
            risk = "status-flip risk"
        elif len({c[2] for c in cands}) > 1:
            risk = "evidence risk"
        else:
            risk = "resource-id only"
        status = A.audit(cid)["deletion_status"]
        out[(risk, "eval" if cid in eval_ids else "sample", status)] += 1
        rows.append((cid, status, risk, cands))
    print(f"ambiguous account slots: {len(rows)}")
    for k, v in sorted(out.items()):
        print(f"  {k[0]:18} {k[1]:6} {k[2]:10} {v}")
    print("candidate state patterns:", dict(Counter(tuple(sorted(str(c[1]) for c in r[3])) for r in rows)))
    print("candidates observed <=24h before audit_at:", sum(1 for r in rows for c in r[3] if c[3]),
          "| carrying any user's identifiers:", sum(1 for r in rows for c in r[3] if c[4]))
    for cid, status, risk, cands in rows:
        if risk != "resource-id only":
            print(f"  {cid} {status} {risk}: " + ", ".join(f"{c[0]}={c[1]}/{c[2] or '-'}" for c in cands))


if __name__ == "__main__":
    main()
