"""Grader: validate an output CSV against the contract and score it against gold answers.

    python code/evaluation/grade.py                       # output.csv vs dataset/sample_output.csv
    python code/evaluation/grade.py --pred P --gold G     # any pair of files
    python code/evaluation/grade.py --json                # machine-readable

Only cases present in the gold file are scored (sample cases for the public gold).
The hidden grader's exact formula is unknown, so several complementary metrics are reported:

  status accuracy     deletion_status equal
  step P/R/F1         multiset match on (phase, system_id, resource_id, operation, state)
  evidence accuracy   among matched steps, evidence_id equal
  exact-step F1       same, but evidence_id must match too
  field accuracy      each gold step aligned to the best predicted step of the same resource and
                      phase; per field, the share of gold steps whose field is reproduced
  resource recall     share of gold (case, resource_id) pairs present anywhere in the prediction
  sequence accuracy   cases whose full ordered step list (all 7 fields) equals gold;
                      order accuracy - among cases with the same step set, same order
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OPS = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"), "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"), "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"), "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"), "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"), "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"), "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"), "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
OP_ORDER = list(OPS)
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}


def load(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {r["case_id"]: r for r in rows}


# ------------------------------------------------------------------ contract validation
def validate(path: Path, expected_ids: list[str] | None = None) -> list[str]:
    errs = []
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errs.append(f"header {header} != case_id,deletion_status,steps_json")
        return errs
    ids = [r[0] for r in rows]
    if ids != sorted(ids, key=lambda c: (len(c), c)) and ids != sorted(ids):
        errs.append("rows are not sorted by case_id")
    if len(set(ids)) != len(ids):
        errs.append("duplicate case_id rows")
    if expected_ids is not None:
        missing, extra = set(expected_ids) - set(ids), set(ids) - set(expected_ids)
        if missing or extra or len(rows) != len(expected_ids):
            errs.append(f"row set mismatch: {len(rows)} rows, missing {len(missing)}, extra {len(extra)}")
    for cid, status, sj in rows:
        try:
            steps = json.loads(sj)
        except json.JSONDecodeError as e:
            errs.append(f"{cid}: steps_json invalid JSON ({e})")
            continue
        errs += [f"{cid}: {m}" for m in validate_case(status, steps)]
    return errs


def validate_case(status: str, steps) -> list[str]:
    out = []
    if status not in STATUSES:
        out.append(f"bad status {status!r}")
    if not isinstance(steps, list):
        return out + ["steps_json is not an array"]
    if status == "NOT_APPLICABLE" and steps:
        out.append("NOT_APPLICABLE must have []")
    if status != "NOT_APPLICABLE" and not steps:
        out.append("deletion case with no steps")
    last = (-1, -1, "", "")
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or set(s) != set(FIELDS):
            out.append(f"step {i}: fields {sorted(s) if isinstance(s, dict) else s} != {FIELDS}")
            continue
        if s["step_no"] != i:
            out.append(f"step {i}: step_no {s['step_no']}")
        op = s["operation"]
        if op not in OPS:
            out.append(f"step {i}: unknown operation {op}")
            continue
        ph, st = OPS[op]
        if s["phase"] != ph or s["state"] != st:
            out.append(f"step {i}: {op} needs phase {ph} / state {st}, got {s['phase']} / {s['state']}")
        key = (PHASES.index(ph), OP_ORDER.index(op), s["system_id"], s["resource_id"])
        if key < last:
            out.append(f"step {i}: out of order")
        last = key
        if status == "COMPLETE" and ph != "VERIFICATION":
            out.append(f"step {i}: COMPLETE case contains {ph}")
        needs_ev = ph == "FINDING" or status == "COMPLETE"
        if needs_ev and not s["evidence_id"]:
            out.append(f"step {i}: {ph} in {status} case must cite evidence")
        if not needs_ev and s["evidence_id"]:
            out.append(f"step {i}: future {ph} step must have empty evidence_id")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps if isinstance(s, dict)):
        out.append("INCOMPLETE case without findings")
    return out


# ------------------------------------------------------------------ scoring
def core(s):
    return (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"])


def full(s):
    return tuple(s[f] for f in FIELDS)


def score(pred: dict[str, dict], gold: dict[str, dict]) -> dict:
    m = Counter()
    field_hits = Counter()
    per_case = {}
    status_conf = Counter()
    for cid, g in gold.items():
        p = pred.get(cid)
        gs = json.loads(g["steps_json"])
        ps = json.loads(p["steps_json"]) if p else []
        pstat = p["deletion_status"] if p else "MISSING"
        status_conf[(g["deletion_status"], pstat)] += 1
        m["cases"] += 1
        m["status_ok"] += pstat == g["deletion_status"]
        gc, pc = Counter(map(core, gs)), Counter(map(core, ps))
        tp = sum((gc & pc).values())
        m["tp"] += tp
        m["gold_steps"] += sum(gc.values())
        m["pred_steps"] += sum(pc.values())
        gf, pf = Counter(map(full, gs)), Counter(map(full, ps))
        m["tp_full"] += sum((gf & pf).values())
        # evidence among core-matched steps
        pev = {}
        for s in ps:
            pev.setdefault(core(s), []).append(s["evidence_id"])
        for s in gs:
            lst = pev.get(core(s))
            if lst:
                m["ev_matched"] += 1
                m["ev_ok"] += s["evidence_id"] in lst
        # field alignment: best predicted step with same resource and phase
        for s in gs:
            cands = [q for q in ps if q["resource_id"] == s["resource_id"] and q["phase"] == s["phase"]] \
                or [q for q in ps if q["resource_id"] == s["resource_id"]]
            best = max(cands, key=lambda q: sum(q[f] == s[f] for f in FIELDS), default=None)
            for f in FIELDS:
                field_hits[f] += bool(best) and best[f] == s[f]
        gr = {s["resource_id"] for s in gs}
        m["res_gold"] += len(gr)
        m["res_hit"] += len(gr & {s["resource_id"] for s in ps})
        exact = pstat == g["deletion_status"] and [full(s) for s in gs] == [full(s) for s in ps]
        m["seq_exact"] += exact
        if gc == pc:
            m["same_set"] += 1
            m["same_order"] += [core(s) for s in gs] == [core(s) for s in ps]
        per_case[cid] = dict(status=(g["deletion_status"], pstat), tp=tp, gold=len(gs), pred=len(ps), exact=exact,
                             missing=sorted(map(list, (gc - pc).elements())), extra=sorted(map(list, (pc - gc).elements())))
    prec = m["tp"] / m["pred_steps"] if m["pred_steps"] else 0.0
    rec = m["tp"] / m["gold_steps"] if m["gold_steps"] else 0.0
    precf = m["tp_full"] / m["pred_steps"] if m["pred_steps"] else 0.0
    recf = m["tp_full"] / m["gold_steps"] if m["gold_steps"] else 0.0
    f1 = lambda a, b: 2 * a * b / (a + b) if a + b else 0.0          # noqa: E731
    return {
        "cases": m["cases"],
        "status_accuracy": m["status_ok"] / m["cases"],
        "status_confusion": {f"{g}->{p}": n for (g, p), n in sorted(status_conf.items())},
        "step_precision": prec, "step_recall": rec, "step_f1": f1(prec, rec),
        "exact_step_precision": precf, "exact_step_recall": recf, "exact_step_f1": f1(precf, recf),
        "evidence_accuracy": m["ev_ok"] / m["ev_matched"] if m["ev_matched"] else 0.0,
        "field_accuracy": {f: field_hits[f] / m["gold_steps"] for f in FIELDS} if m["gold_steps"] else {},
        "resource_recall": m["res_hit"] / m["res_gold"] if m["res_gold"] else 0.0,
        "sequence_exact_accuracy": m["seq_exact"] / m["cases"],
        "order_accuracy_given_same_set": m["same_order"] / m["same_set"] if m["same_set"] else 0.0,
        "counts": dict(m),
        "per_case": per_case,
    }


def render(res: dict, errors: list[str]) -> str:
    lines = [f"cases scored: {res['cases']}",
             f"contract errors: {len(errors)}" + ("".join("\n  - " + e for e in errors[:20]))]
    for k in ("status_accuracy", "step_precision", "step_recall", "step_f1", "evidence_accuracy",
              "exact_step_f1", "resource_recall", "sequence_exact_accuracy", "order_accuracy_given_same_set"):
        lines.append(f"{k:32} {res[k]:.4f}")
    lines.append("field accuracy: " + ", ".join(f"{f}={v:.3f}" for f, v in res["field_accuracy"].items()))
    lines.append("status confusion (gold->pred): " + json.dumps(res["status_confusion"]))
    bad = {c: v for c, v in res["per_case"].items() if not v["exact"]}
    lines.append(f"cases not exact: {len(bad)}")
    for c, v in sorted(bad.items(), key=lambda x: (len(x[0]), x[0])):
        lines.append(f"  {c} status {v['status'][0]}->{v['status'][1]} tp {v['tp']}/{v['gold']} pred {v['pred']}")
        for s in v["missing"]:
            lines.append(f"      - missing {s}")
        for s in v["extra"]:
            lines.append(f"      + extra   {s}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=str(REPO / "output.csv"))
    ap.add_argument("--gold", default=str(REPO / "dataset" / "sample_output.csv"))
    ap.add_argument("--expect", default=str(REPO / "dataset" / "deletion_requests.csv"),
                    help="request file whose case_ids the prediction must cover exactly ('' to skip)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    expected = None
    if a.expect:
        with open(a.expect, encoding="utf-8", newline="") as fh:
            expected = [r["case_id"] for r in csv.DictReader(fh)]
    errors = validate(Path(a.pred), expected)
    res = score(load(Path(a.pred)), load(Path(a.gold)))
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "per_case"} | {"contract_errors": errors}, indent=2))
    else:
        print(render(res, errors))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
