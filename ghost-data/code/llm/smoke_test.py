"""Smoke test of both providers: models, one text + one image request each, limits, JSON, exact reads.

Run:  python code/llm/smoke_test.py            (never prints key values)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.client import REPO, complete, list_models          # noqa: E402

IMAGE = REPO / "dataset" / "evidence" / "images" / "backup_restore_console_20260429.png"
TEXT_CANDIDATES = [("groq", "openai/gpt-oss-120b"), ("groq", "openai/gpt-oss-20b"), ("groq", "qwen/qwen3.8-27b"),
                   ("gemini", "gemini-3.5-flash-lite"), ("gemini", "gemini-3.5-flash"), ("gemini", "gemini-3.8-flash")]
VISION_CANDIDATES = [("gemini", "gemini-3.5-flash"), ("gemini", "gemini-3.5-flash-lite"), ("gemini", "gemini-3.8-flash"),
                     ("groq", "qwen/qwen3.8-27b")]
# gemini-2.5-flash / -flash-lite: HTTP 404 "no longer available to new users" (2026-10-25)
EXPECT = ["backup_9287", "areed1433@gmail.com", "Retention end", "26 Jul 2026"]
TEXT_PROMPT = 'Return JSON {"deletion": true|false} for this request: "Please erase the account linked to this email address."'
VISION_PROMPT = ("Transcribe this admin-console screenshot. Return JSON: {\"rows\": [{\"resource_id\": str, "
                 "\"account_email\": str, \"cells\": [str]}], \"detail_panel\": {\"label\": \"value\"}, "
                 "\"all_text\": [str]} copying every id, email and date exactly as shown.")


def run(provider, model, prompt, image=None):
    try:
        r = complete(provider, model, prompt, image=image, max_tokens=4096, use_cache=False, purpose="smoke")
    except Exception as e:                                   # report and continue
        return {"provider": provider, "model": model, "error": str(e)[:200]}
    out = {"provider": provider, "model": model, "latency_s": r.latency_s, "input_tokens": r.input_tokens,
           "output_tokens": r.output_tokens, "rate_headers": r.headers}
    try:
        parsed = r.json()
        out["json_ok"] = True
    except Exception:
        parsed, out["json_ok"] = None, False
    if image:
        blob = json.dumps(parsed, ensure_ascii=False) if parsed is not None else r.text
        out["exact_reads"] = {e: (e in blob) for e in EXPECT}
    else:
        out["answer"] = parsed
    return out


def main():
    report = {"models": {}, "text": [], "vision": []}
    for p in ("groq", "gemini"):
        try:
            report["models"][p] = list_models(p)
        except Exception as e:
            report["models"][p] = f"error: {str(e)[:120]}"
    for p, m in TEXT_CANDIDATES:
        report["text"].append(run(p, m, TEXT_PROMPT))
    for p, m in VISION_CANDIDATES:
        report["vision"].append(run(p, m, VISION_PROMPT, IMAGE))
    out = REPO / ".cache" / "smoke_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for kind in ("text", "vision"):
        print(f"== {kind}")
        for r in report[kind]:
            print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
