"""Run the pipeline on the participant-written custom cases and grade them.

Each custom case (code/evaluation/custom_cases/cases.json) is a variant of one existing request:
that request is removed and the variant takes its place, so the global workflow solve still sees a
consistent set of requests. Expected rows (expected.csv) were derived by hand from the evidence
(or are the published sample rows when the variant must not change the answer).
Screenshot extractions come from the LLM cache. Intent is decided by the rules, as in the pipeline.
Run (repo venv):  .venv/Scripts/python code/evaluation/custom_eval.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from audit.data import REPO_ROOT, Dataset     # noqa: E402
from audit.pipeline import Auditor            # noqa: E402
import grade as G                             # noqa: E402

_spec = importlib.util.spec_from_file_location("appmain", HERE.parent / "main.py")
appmain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(appmain)


def main():
    cases = json.loads((HERE / "custom_cases" / "cases.json").read_text(encoding="utf-8"))
    from llm.vision import extract_all
    images = extract_all(Dataset.load(), log_name="vision_dropped_custom.jsonl")
    results = []
    for c in cases:                      # one solve per case: variants of the same request never compete
        ds = Dataset.load()
        ds.requests = [r for r in ds.requests if r["case_id"] != c["replaces"]]
        ds.samples = [r for r in ds.samples if r["case_id"] != c["replaces"]]
        ds.requests.append(c["request"])
        results.append(Auditor(ds, image_extractions=images).audit(c["case_id"]))
    out = REPO_ROOT / "runs" / "custom_output.csv"
    appmain.write_csv(out, results)
    appmain.write_trace(REPO_ROOT / "runs" / "trace_custom.jsonl", results)
    gold = G.load(HERE / "custom_cases" / "expected.csv")
    res = G.score(G.load(out), gold)
    errs = G.validate(out)
    print(f"custom cases: {len(cases)}  contract errors: {len(errs)}")
    for k in ("status_accuracy", "step_precision", "step_recall", "step_f1", "evidence_accuracy",
              "sequence_exact_accuracy"):
        print(f"  {k:24} {res[k]:.3f}")
    for c in cases:
        pc = res["per_case"][c["case_id"]]
        mark = "PASS" if pc["exact"] else "FAIL"
        print(f"\n{c['case_id']} [{mark}] replaces {c['replaces']}: {c['targets']}")
        print(f"   status expected {pc['status'][0]} got {pc['status'][1]}; steps {pc['tp']}/{pc['gold']} matched, {pc['pred']} predicted")
        for s in pc["missing"]:
            print(f"   - missing {s}")
        for s in pc["extra"]:
            print(f"   + extra   {s}")
        if not pc["missing"] and not pc["extra"] and not pc["exact"]:
            print("   (same step set; evidence_id or order differs)")


if __name__ == "__main__":
    main()
