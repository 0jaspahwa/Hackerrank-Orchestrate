"""Consistency gate: refuses to emit inconsistent rows (called by main.py before writing).

Every check is re-derived independently of the step-emission code:
  STATUS_PHASES   COMPLETE = verification only, all cited; INCOMPLETE has >= 1 finding;
                  NOT_APPLICABLE = []; a deletion row is never empty
  CHAIN           every finding has its remediation; every remediated resource has a verification
  OP_STATE        operation <-> phase / state pairs from the allowed-operations table
  TREATMENT       the operations used for a resource match the treatment that deletion_rules.csv
                  requires for its category (re-mapped here from the rules file)
  EVIDENCE        cited id exists, is dated <= audit_at, and observes / mentions the resource
                  (a file observation, a catalog artifact, an account-store snapshot proving absence,
                  or an activity row about that resource); remediation and INCOMPLETE verification
                  steps carry no evidence
  ORDER           phase order, operation-table order, system_id, resource_id; step_no 1..n
  CROSS_USER      no resource appears in two different users' rows
"""
from __future__ import annotations

from collections import defaultdict

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OPS = [
    ("REPORT_REMAINING_RECORD", "FINDING", "PRESENT"), ("REPORT_EXPOSED_PII", "FINDING", "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", "FINDING", "ACTIVE"), ("REPORT_RESTORE_RISK", "FINDING", "ACTIVE"),
    ("DISABLE_DATA_FLOW", "REMEDIATION", "DISABLED"), ("BLOCK_RESTORE", "REMEDIATION", "BLOCKED"),
    ("DELETE_RECORD", "REMEDIATION", "ABSENT"), ("REDACT_RECORD", "REMEDIATION", "REDACTED"),
    ("ANONYMIZE_RECORD", "REMEDIATION", "ANONYMIZED"),
    ("VERIFY_RECORD_ABSENT", "VERIFICATION", "ABSENT"), ("VERIFY_RECORD_PRESENT", "VERIFICATION", "PRESENT"),
    ("VERIFY_RECORD_REDACTED", "VERIFICATION", "REDACTED"), ("VERIFY_RECORD_ANONYMIZED", "VERIFICATION", "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", "VERIFICATION", "DISABLED"), ("VERIFY_RESTORE_BLOCKED", "VERIFICATION", "BLOCKED"),
]
OP = {o: (ph, st) for o, ph, st in OPS}
OP_IDX = {o: i for i, (o, _, _) in enumerate(OPS)}
FIX_FOR = {"REPORT_REMAINING_RECORD": {"DELETE_RECORD"}, "REPORT_EXPOSED_PII": {"REDACT_RECORD", "ANONYMIZE_RECORD"},
           "REPORT_ACTIVE_DATA_FLOW": {"DISABLE_DATA_FLOW"}, "REPORT_RESTORE_RISK": {"BLOCK_RESTORE"}}
# required action (deletion_rules.csv) -> operations a resource of that category may carry
ALLOWED_BY_ACTION = {
    "DELETE": {"REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"},
    "REDACT": {"REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"},
    "ANONYMIZE": {"REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"},
    "RETAIN": {"VERIFY_RECORD_PRESENT"},
}
BACKUP_OPS = {"REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"}
FLOW_OPS = {"REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"}


def check(results: list[dict], auditor) -> list[tuple]:
    """-> [(case_id, code, detail)]; empty means every row may be written."""
    ds, ix = auditor.ds, auditor.ix
    rules = {r["data_category"]: r["required_action"] for r in ds.rules}
    acts = {a["activity_id"]: a for a in ds.activity}
    store_files = {s.file_id: s for s in ix.stores}
    v = []
    owner = defaultdict(set)
    for res in results:
        cid, st, steps = res["case_id"], res["deletion_status"], res["steps"]
        req = auditor.R[cid]
        audit = req["audit_at"]
        trace_res = res.get("trace", {}).get("resources", {})
        uid = res.get("trace", {}).get("user_id")

        def bad(code, detail):
            v.append((cid, code, detail))

        # STATUS_PHASES
        phases = [s.get("phase") for s in steps]
        if st == "NOT_APPLICABLE":
            if steps:
                bad("STATUS_PHASES", "NOT_APPLICABLE with steps")
            continue
        if st not in ("COMPLETE", "INCOMPLETE"):
            bad("STATUS_PHASES", f"unknown status {st!r}")
            continue
        if not steps:
            bad("STATUS_PHASES", "deletion row without steps")
        if st == "COMPLETE" and any(p != "VERIFICATION" for p in phases):
            bad("STATUS_PHASES", "COMPLETE row contains FINDING/REMEDIATION")
        if st == "COMPLETE" and any(not s.get("evidence_id") for s in steps):
            bad("STATUS_PHASES", "COMPLETE verification without evidence")
        if st == "INCOMPLETE" and "FINDING" not in phases:
            bad("STATUS_PHASES", "INCOMPLETE row without a finding")
        # ORDER / OP_STATE
        last = None
        for i, s in enumerate(steps, 1):
            if s.get("step_no") != i:
                bad("ORDER", f"step_no {s.get('step_no')} at position {i}")
            op = s.get("operation")
            if op not in OP:
                bad("OP_STATE", f"unknown operation {op}")
                continue
            if (s.get("phase"), s.get("state")) != OP[op]:
                bad("OP_STATE", f"{op} with phase/state {s.get('phase')}/{s.get('state')}")
            key = (PHASES.index(OP[op][0]), OP_IDX[op], s.get("system_id"), s.get("resource_id"))
            if last is not None and key < last:
                bad("ORDER", f"step {i} out of order")
            last = key
        # CHAIN
        by_phase = defaultdict(dict)
        for s in steps:
            by_phase[s.get("phase")].setdefault(s.get("resource_id"), []).append(s.get("operation"))
        for rid, ops in by_phase["FINDING"].items():
            fixes = set(by_phase["REMEDIATION"].get(rid, []))
            if not any(FIX_FOR.get(o, set()) & fixes for o in ops):
                bad("CHAIN", f"finding on {rid} without its remediation")
        for rid in by_phase["REMEDIATION"]:
            if rid not in by_phase["VERIFICATION"]:
                bad("CHAIN", f"remediated {rid} without verification")
            if rid not in by_phase["FINDING"]:
                bad("CHAIN", f"remediation on {rid} without a finding")
        # TREATMENT
        for rid in {s.get("resource_id") for s in steps}:
            ops = {s.get("operation") for s in steps if s.get("resource_id") == rid}
            cat = trace_res.get(rid, {}).get("category")
            if cat == "BACKUP_ARCHIVE":
                allowed = BACKUP_OPS
            elif cat == "DATA_FLOW":
                allowed = FLOW_OPS
            elif cat == "ACCOUNT_TOMBSTONE":
                allowed = ALLOWED_BY_ACTION["REDACT"]
            elif cat in rules and rules[cat] in ALLOWED_BY_ACTION:
                allowed = ALLOWED_BY_ACTION[rules[cat]]
            elif cat:
                allowed = ALLOWED_BY_ACTION["DELETE"]     # documented fallback for a rule gap (Policy.treatment)
            else:
                bad("TREATMENT", f"{rid}: no category in trace")
                continue
            if not ops <= allowed:
                bad("TREATMENT", f"{rid} ({cat} -> {rules.get(cat, cat)}) carries {sorted(ops - allowed)}")
        # EVIDENCE
        for s in steps:
            ev, rid, ph = s.get("evidence_id", ""), s.get("resource_id"), s.get("phase")
            needs = ph == "FINDING" or st == "COMPLETE"
            if not needs:
                if ev:
                    bad("EVIDENCE", f"future {ph} step on {rid} carries evidence {ev}")
                continue
            if not ev:
                bad("EVIDENCE", f"{ph} step on {rid} has no evidence")
                continue
            if ev in acts:
                a = acts[ev]
                if a["event_time"] > audit:
                    bad("EVIDENCE", f"{ev} dated after audit_at")
                if rid not in (a["resource_id"], *a["details"].split()) and rid not in a["details"]:
                    bad("EVIDENCE", f"{ev} is not about {rid}")
                continue
            m = ds.manifest.get(ev)
            if m is None:
                bad("EVIDENCE", f"unknown evidence id {ev}")
                continue
            if m["captured_at"] > audit:
                bad("EVIDENCE", f"{ev} captured after audit_at")
            observes = any(o.file_id == ev for o in ix.by_rid.get(rid, ()))
            absence = (s.get("operation") == "VERIFY_RECORD_ABSENT" and rid.startswith("account_")
                       and ev in store_files and rid not in store_files[ev].resource_ids)
            if not (observes or absence):
                bad("EVIDENCE", f"{ev} neither observes {rid} nor proves its absence")
        for s in steps:
            owner[s.get("resource_id")].add((uid, cid))
    # CROSS_USER
    for rid, us in owner.items():
        users = {u for u, _ in us}
        if len(users) > 1:
            for _, cid in us:
                v.append((cid, "CROSS_USER", f"{rid} also in rows of {sorted(users)}"))
    return v
