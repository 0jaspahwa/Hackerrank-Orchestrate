"""Precision / recall of the corroborated screenshot extraction against the hand-written fixtures.

Fixtures (code/evaluation/fixtures/screenshots.json) are test-only. Compared per image as a set of
typed tokens (rid:, email:, user:, phone:), plus the state the pipeline derives for the
catalog-linked resource.
Run (repo venv for OCR):  .venv/Scripts/python code/evaluation/vision_eval.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.data import Dataset              # noqa: E402
from llm.vision import extract_all          # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "screenshots.json"


def tokens(d: dict, from_fixture: bool) -> set:
    t = set()
    if from_fixture:
        t |= {"rid:" + x for x in d["resource_ids"]} | {"email:" + x for x in d["emails"]}
        t |= {"user:" + x for x in d["user_ids"]} | {"phone:" + re.sub(r"\D", "", x) for x in d["phones"]}
        return t
    rids = set(d.get("all_resource_ids", [])) | {r["resource_id"] for r in d.get("records", []) if r["resource_id"]}
    emails = set(d.get("all_emails", [])) | set(d.get("page_subject", {}).get("emails", []))
    users = set(d.get("all_user_ids", [])) | set(d.get("page_subject", {}).get("user_ids", []))
    phones = set(d.get("all_phones", [])) | set(d.get("page_subject", {}).get("phones", []))
    for r in d.get("records", []):
        emails |= set(r["emails"])
        users |= set(r["user_ids"])
        phones |= set(r["phones"])
    t |= {"rid:" + x for x in rids} | {"email:" + x for x in emails} | {"user:" + x for x in users}
    t |= {"phone:" + re.sub(r"\D", "", x) for x in phones}
    return t


def main():
    ds = Dataset.load()
    models = [("gemini", "gemini-3.5-flash"), ("groq", "qwen/qwen3.8-27b")]
    if len(sys.argv) > 1:
        models = [tuple(a.split(":", 1)) for a in sys.argv[1:]]
    for provider, model in models:              # each model alone: no fallback while scoring
        print(f"######## {provider}:{model}")
        score(ds, extract_all(ds, models=[(provider, model)], log_name=f"vision_dropped_{model.replace('/', '_')}.jsonl"))


def score(ds, ext):
    fx = {k: v for k, v in json.loads(FIX.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    from audit.pipeline import Auditor
    A = Auditor(ds, image_extractions=ext)
    tp = fp = fn = 0
    state_ok = 0
    print(f"{'image':12} {'P':>5} {'R':>5}  {'linked resource':16} {'expected':10} {'derived':10} model / dropped / missing / extra")
    for fid, f in sorted(fx.items()):
        exp, got = tokens(f, True), tokens(ext.get(fid, {}), False)
        a, b, c = len(exp & got), len(got - exp), len(exp - got)
        tp, fp, fn = tp + a, fp + b, fn + c
        p = a / (a + b) if a + b else 1.0
        r = a / (a + c) if a + c else 1.0
        rid = f["linked"]["resource_id"]
        obs = [o for o in A.ix.by_rid.get(rid, ()) if o.file_id == fid and o.source_kind == "artifact"]
        from audit.state import verdict
        derived = verdict(obs[0]) if obs else None
        state_ok += derived == f["linked"]["state"]
        e = ext.get(fid, {})
        print(f"{fid:12} {p:5.2f} {r:5.2f}  {rid:16} {f['linked']['state']:10} {str(derived):10} "
              f"{e.get('model')} / dropped {[d['value'] for d in e.get('dropped', [])]} / missing {sorted(exp - got)} / extra {sorted(got - exp)}")
    P, R = tp / (tp + fp) if tp + fp else 1.0, tp / (tp + fn) if tp + fn else 1.0
    print(f"overall token precision {P:.3f} recall {R:.3f} ({tp} tp, {fp} fp, {fn} fn); linked-resource state {state_ok}/{len(fx)}")


if __name__ == "__main__":
    main()
