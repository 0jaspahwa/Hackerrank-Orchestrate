"""Ghost Data Still Exists - deletion-audit agent.

    python code/main.py                              # all 518 eval requests -> ./output.csv
    python code/main.py --samples                    # 32 sample requests -> runs/sample_output.csv
    python code/main.py --case case_05 --explain     # human-readable trace for one case
Traces (one JSON object per case) go to runs/trace*.jsonl. Everything stays inside the repo.
"""
from __future__ import annotations

import argparse
import os
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit.data import REPO_ROOT                 # noqa: E402
from audit.data import InputError                # noqa: E402
from audit.pipeline import Auditor               # noqa: E402

RUNS = REPO_ROOT / "runs"


def natural(cid: str):
    head, _, num = cid.rpartition("_")
    return (head, int(num)) if num.isdigit() else (cid, 0)


def write_csv(path: Path, results: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in sorted(results, key=lambda r: natural(r["case_id"])):
            w.writerow([r["case_id"], r["deletion_status"],
                        json.dumps(r["steps"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)])


def write_trace(path: Path, results: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in sorted(results, key=lambda r: natural(r["case_id"])):
            fh.write(json.dumps({**r["trace"], "deletion_status": r["deletion_status"], "steps": r["steps"]},
                                sort_keys=True, ensure_ascii=False) + "\n")


def explain(res: dict) -> str:
    t = res["trace"]
    out = [f"{t['case_id']}  {t['email']} ({t['user_id']})  reported {t['reported_status']}  audit_at {t['audit_at']}",
           f"intent: {'deletion' if t['intent']['deletion'] else 'NON-deletion'} - {t['intent']['reason']}"
           + (f"  [CLASSIFY_REQUEST {t['intent']['classify_request']}]" if t["intent"].get("classify_request") else "")]
    for rid, x in sorted(t.get("excluded_prior_episode", {}).items()):
        out.append(f"  excluded {rid}: links {x['links'][:2]} belong to prior episode ({'; '.join(x['prior_episode'])})")
    for w in t.get("workflow", []):
        out.append(f"  workflow {w['activity']} -> {w['resource']} [{w['how']}]"
                   + (f" alternatives {w['alternatives']}" if w["alternatives"] else ""))
    if t.get("open_account_candidates"):
        out.append(f"  open account candidates before tie-break: {t['open_account_candidates']}")
    for rid, x in sorted(t.get("resources", {}).items()):
        out.append(f"  {rid:22} {x['category']:20} {x['treatment']:9} state={x['state']!s:10} cite={x['cite'] or '-':12}"
                   f" ({x['category_reason']})")
        out.append(f"      linked by: {'; '.join(x['linked_by'])}")
        if x["basis"]:
            out.append(f"      state from: {'; '.join(x['basis'])}" + ("  [post-capture file]" if x["post_capture_only"] else ""))
    out.append(f"=> {res['deletion_status']}")
    for s in res["steps"]:
        out.append(f"  {s['step_no']:>2} {s['phase']:12} {s['operation']:26} {s['system_id']:20} {s['resource_id']:18} {s['evidence_id']}")
    for f in t["flags"]:
        out.append(f"  ! {f}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", action="store_true", help="run the 32 sample requests instead of the 518")
    ap.add_argument("--case", action="append", help="only these case ids (repeatable)")
    ap.add_argument("--explain", action="store_true", help="print a readable trace for the selected cases")
    ap.add_argument("--output", help="output CSV path")
    ap.add_argument("--no-llm", action="store_true", help="rules only: no intent confirmation, no screenshot extraction")
    ap.add_argument("--strict", action="store_true",
                    help="abort without writing output if any LLM call fails or any fallback is used "
                         "(secondary provider, rules-only intent, OCR-only vision)")
    ap.add_argument("--adopt-investigator", action="store_true",
                    help="apply row changes caused by validator-accepted investigator facts (default: report only)")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="ignore code/llm/cache_snapshot/ (force live calls for entries missing from .cache/llm)")
    a = ap.parse_args(argv)
    if a.no_snapshot:
        os.environ["LLM_NO_SNAPSHOT"] = "1"
    if a.strict and a.no_llm:
        ap.error("--strict requires the LLM layer (drop --no-llm)")
    try:
        from audit.data import Dataset
        ds_main = Dataset.load()
    except InputError as e:
        print(f"INPUT ERROR (output not written): {e}", file=sys.stderr)
        return 2
    images, intent_llm = None, None
    if a.strict:
        os.environ["LLM_STRICT"] = "1"
    if not a.no_llm:
        from llm import client, intent_llm as il, vision
        ds = ds_main
        try:
            images = vision.extract_all(ds)
            reqs = ds.samples + ds.requests
            texts = {r["case_id"]: r["request_text"] for r in reqs}
            intent_llm = il.classify_all(texts)
        except client.StrictAbort as e:
            print(f"STRICT MODE ABORT (output not written): {e}", file=sys.stderr)
            return 2
        if a.strict:
            from llm.models import TEXT, VISION
            pinned_v, pinned_t = f"{VISION[0][0]}:{VISION[0][1]}", f"{TEXT[0][0]}:{TEXT[0][1]}"
            bad = [f"vision {fid}: {e.get('model') or 'no model answer (OCR-only)'}" for fid, e in sorted(images.items())
                   if e.get("model") != pinned_v]
            bad += sorted({f"intent: {v.get('model') or 'no model answer (rules-only)'}" for v in intent_llm.values()
                           if v.get("model") != pinned_t})
            if bad:
                print("STRICT MODE ABORT (output not written):\n  " + "\n  ".join(bad[:20]), file=sys.stderr)
                return 2
    auditor = Auditor(ds_main, image_extractions=images, intent_llm=intent_llm)
    if auditor.ix.load_errors:
        for fid, err in auditor.ix.load_errors:
            print(f"evidence skipped: {fid}: {err}", file=sys.stderr)
        if a.strict:
            print("STRICT MODE ABORT (output not written): evidence files failed to load", file=sys.stderr)
            return 2
    for cid, fl in sorted(auditor.ds.input_flags.items()):
        print(f"input handled conservatively: {cid}: {'; '.join(fl)[:200]}", file=sys.stderr)

    if intent_llm:
        from llm import intent_llm as il
        il.write_disagreements(auditor.intent, intent_llm, {c: r["request_text"] for c, r in auditor.R.items()})
    if a.case:
        ids = a.case
    else:
        ids = [r["case_id"] for r in (auditor.ds.samples if a.samples else auditor.ds.requests)]
    results = [auditor.audit(cid) for cid in ids]
    if not a.no_llm and not a.samples:
        # bounded investigator: read-only, as_of = audit_at, validator-gated facts only
        from agent.investigator import Investigator, summarize
        from llm import client as _client
        try:
            records, extra = Investigator(auditor, {r["case_id"]: r for r in results}).run()
        except _client.StrictAbort as e:
            print(f"STRICT MODE ABORT (output not written): {e}", file=sys.stderr)
            return 2
        for r in results:
            if r["case_id"] in records:
                r["trace"]["investigator"] = records[r["case_id"]]
        summ = summarize(records)
        print(f"investigator: {json.dumps(summ, sort_keys=True)}", file=sys.stderr)
        changed = []
        if extra:
            alt = Auditor(ds_main, image_extractions=images, intent_llm=intent_llm, extra_links=extra)
            alt_results = [alt.audit(cid) for cid in ids]
            before = {r["case_id"]: (r["deletion_status"], r["steps"]) for r in results}
            changed = [x["case_id"] for x in alt_results if before[x["case_id"]] != (x["deletion_status"], x["steps"])]
            print(f"investigator: accepted facts {extra} change {len(changed)} rows: {changed}", file=sys.stderr)
            if a.adopt_investigator:
                for x in alt_results:
                    x["trace"]["investigator"] = records.get(x["case_id"])
                results = alt_results
            elif changed:
                print("investigator facts NOT adopted (re-run with --adopt-investigator after review)", file=sys.stderr)
        RUNS.mkdir(parents=True, exist_ok=True)
        (RUNS / "investigator.json").write_text(json.dumps({"summary": summ, "accepted_facts": extra,
                                                           "rows_changed_if_adopted": changed,
                                                           "adopted": bool(a.adopt_investigator and changed),
                                                           "records": records}, indent=1, ensure_ascii=False),
                                                encoding="utf-8")
    if a.explain:
        print("\n\n".join(explain(r) for r in results))
        return 0
    if a.case:
        out = Path(a.output) if a.output else RUNS / "cases_output.csv"
        trace = RUNS / "trace_cases.jsonl"
    elif a.samples:
        out = Path(a.output) if a.output else RUNS / "sample_output.csv"
        trace = RUNS / "trace_samples.jsonl"
    else:
        out = Path(a.output) if a.output else REPO_ROOT / "output.csv"
        trace = RUNS / ("trace.jsonl" if not a.output else f"trace_{Path(a.output).stem}.jsonl")
    if auditor.policy.fallbacks:
        print(f"deletion_rules gaps handled as DELETE: {sorted(auditor.policy.fallbacks)}", file=sys.stderr)
    # human-review queue: rows answered NOT_APPLICABLE only because nothing could be proven (D52)
    review = [r for r in results if r["trace"].get("review")]
    RUNS.mkdir(parents=True, exist_ok=True)
    review_path = (Path(a.output).parent if a.output else RUNS) / "human_review.csv"
    with open(review_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "reason_code", "missing"])
        for r in sorted(review, key=lambda r: natural(r["case_id"])):
            w.writerow([r["case_id"], r["trace"]["review"]["code"], r["trace"]["review"]["missing"]])
    print(f"human review queue: {len(review)} cases -> {review_path}", file=sys.stderr)
    from audit.consistency import check
    violations = check(results, auditor)                 # consistency gate: never write inconsistent rows
    print(f"consistency gate: {len(results)} rows checked, {len(violations)} violations", file=sys.stderr)
    if violations:
        for cid, code, detail in violations[:50]:
            print(f"  GATE {cid} {code}: {detail}", file=sys.stderr)
        print(f"output NOT written ({out})", file=sys.stderr)
        return 3
    write_csv(out, results)
    write_trace(trace, results)
    if not a.no_llm:
        from llm import client
        usage = RUNS / ("usage.json" if not (a.case or a.samples) else "usage_partial.json")
        usage.write_text(json.dumps(client.USAGE, indent=1), encoding="utf-8")
        (RUNS / "used_cache.json").write_text(json.dumps(sorted(set(client.USED_CACHE)), indent=1), encoding="utf-8")
    print(f"wrote {len(results)} rows to {out} and traces to {trace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
