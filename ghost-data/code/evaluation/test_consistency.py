"""Gate tests: each deliberately broken row must be blocked with the expected violation code.

Run:  python code/evaluation/test_consistency.py      (also collected by pytest)
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.consistency import check          # noqa: E402
from audit.pipeline import Auditor           # noqa: E402

_A = None


def auditor():
    global _A
    if _A is None:
        _A = Auditor()
    return _A


def row(cid):
    return copy.deepcopy(auditor().audit(cid))


def codes(results):
    return {c for _, c, _ in check(results, auditor())}


def mutate(cid, fn):
    r = row(cid)
    fn(r)
    return r


CASES = {   # name -> (expected code, broken rows)
    "complete_with_finding": ("STATUS_PHASES", lambda: [mutate("case_11", lambda r: r.update(deletion_status="COMPLETE"))]),
    "incomplete_without_finding": ("STATUS_PHASES", lambda: [mutate("case_21", lambda r: r.update(deletion_status="INCOMPLETE"))]),
    "not_applicable_with_steps": ("STATUS_PHASES", lambda: [mutate("case_21", lambda r: r.update(deletion_status="NOT_APPLICABLE"))]),
    "finding_without_remediation": ("CHAIN", lambda: [mutate("case_11", lambda r: r.update(
        steps=[s for s in r["steps"] if not (s["phase"] == "REMEDIATION" and s["resource_id"] == "search_052")]))]),
    "remediation_without_verification": ("CHAIN", lambda: [mutate("case_11", lambda r: r.update(
        steps=[s for s in r["steps"] if not (s["phase"] == "VERIFICATION" and s["resource_id"] == "search_052")]))]),
    "wrong_state": ("OP_STATE", lambda: [mutate("case_21", lambda r: r["steps"][0].update(state="PRESENT"))]),
    "treatment_vs_rules": ("TREATMENT", lambda: [mutate("case_21", lambda r: r["steps"][0].update(
        operation="VERIFY_RECORD_REDACTED", state="REDACTED"))]),
    "unknown_evidence": ("EVIDENCE", lambda: [mutate("case_21", lambda r: r["steps"][0].update(evidence_id="evidence_999"))]),
    "evidence_after_audit": ("EVIDENCE", lambda: [mutate("case_21", lambda r: r["steps"][0].update(evidence_id="evidence_02"))]),
    "evidence_not_about_resource": ("EVIDENCE", lambda: [mutate("case_21", lambda r: r["steps"][0].update(evidence_id="evidence_03"))]),
    "future_step_with_evidence": ("EVIDENCE", lambda: [mutate("case_11", lambda r: [s.update(evidence_id="evidence_03")
                                                                                  for s in r["steps"] if s["phase"] == "REMEDIATION"])]),
    "step_no_gap": ("ORDER", lambda: [mutate("case_11", lambda r: r["steps"][2].update(step_no=9))]),
    "misordered": ("ORDER", lambda: [mutate("case_11", lambda r: r.update(steps=[r["steps"][1], r["steps"][0]] + r["steps"][2:]))]),
    "cross_user": ("CROSS_USER", lambda: [row("case_21"), mutate("case_11", lambda r: r["steps"].append(
        {**r["steps"][-1], "resource_id": "account_6440", "system_id": "account_service", "step_no": len(r["steps"]) + 1,
         "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""}))]),
}


def test_valid_rows_pass():
    assert check([row(c) for c in ("case_11", "case_21", "case_31")], auditor()) == []


def test_each_violation_is_blocked():
    for name, (code, build) in CASES.items():
        assert code in codes(build()), name


if __name__ == "__main__":
    ok = True
    print("valid rows pass:", check([row(c) for c in ("case_11", "case_21", "case_31")], auditor()) == [])
    for name, (code, build) in CASES.items():
        got = codes(build())
        passed = code in got
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name:34} expected {code:13} got {sorted(got)}")
    sys.exit(0 if ok else 1)
