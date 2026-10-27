"""LLM confirmation of request intent: batched, deduplicated, cached.

The model only labels each distinct request text. The pipeline keeps the rule verdict
(audit/intent.py) as the decision and records the model's verdict next to it; any disagreement
is flagged in the trace and listed in runs/intent_disagreements.json for review.
"""
from __future__ import annotations

import json
import sys

from .client import REPO, complete
from .models import TEXT

SYSTEM = ("You classify customer privacy requests. A request authorizes DELETION when the person asks to delete, "
          "erase, remove or close their account or personal data. A FOLLOW-UP about an earlier deletion request is also "
          "DELETION: asking to check, inspect, verify, confirm or finish a deletion (e.g. 'my request failed, tell me "
          "whether the account was removed', 'confirm the profile is gone', 'finish the deletion'). It does NOT authorize deletion when it asks only for access, a copy or export, a "
          "correction or update, a login reset, a marketing opt-out, or explicitly says not to delete or close. "
          "Texts may be in any language.")
PROMPT = ('Label each request. Return JSON {"results": [{"id": <id>, "deletion": true|false, "reason": "<= 12 words"}]} '
          "with one entry per id.\n\n")
BATCH = 25


def classify_all(texts: dict[str, str]) -> dict[str, dict]:
    """texts: case_id -> request_text. -> case_id -> {"deletion": bool|None, "reason": str, "model": str}"""
    distinct = sorted(set(texts.values()))
    verdict: dict[str, dict] = {}
    for start in range(0, len(distinct), BATCH):
        chunk = distinct[start:start + BATCH]
        body = "\n".join(json.dumps({"id": i, "text": t}, ensure_ascii=False) for i, t in enumerate(chunk))
        got = None
        for provider, model in TEXT:
            try:
                r = complete(provider, model, PROMPT + body, system=SYSTEM, max_tokens=4096,
                             purpose=f"intent:{start // BATCH}")
                rows = {int(x["id"]): x for x in r.json().get("results", []) if str(x.get("id", "")).isdigit()
                        or isinstance(x.get("id"), int)}
                if len(rows) == len(chunk):
                    got = (rows, f"{provider}:{model}")
                    break
                print(f"[intent] batch {start // BATCH}: {provider}:{model} returned {len(rows)}/{len(chunk)}", file=sys.stderr)
                from . import client as _c
                if _c.STRICT:
                    raise _c.StrictAbort(f"{provider}:{model} answered {len(rows)}/{len(chunk)} items")
            except Exception as e:
                from . import client as _c
                if _c.STRICT:
                    raise _c.StrictAbort(f"{provider}:{m if 'vision' in __name__ else model} failed: {str(e)[:120]}") from e
                print(f"[intent] batch {start // BATCH}: {provider}:{model} failed: {str(e)[:120]}", file=sys.stderr)
        for i, t in enumerate(chunk):
            if got:
                x = got[0][i]
                verdict[t] = {"deletion": bool(x.get("deletion")), "reason": str(x.get("reason", ""))[:120], "model": got[1]}
            else:
                verdict[t] = {"deletion": None, "reason": "no model answer", "model": None}
    return {cid: verdict[t] for cid, t in texts.items()}


def write_disagreements(rule: dict[str, tuple], llm: dict[str, dict], texts: dict[str, str]) -> list:
    out = [{"case_id": c, "rule": rule[c][0], "rule_reason": rule[c][1], "llm": llm[c]["deletion"],
            "llm_reason": llm[c]["reason"], "text": texts[c]}
           for c in sorted(rule) if llm.get(c, {}).get("deletion") is not None and llm[c]["deletion"] != rule[c][0]]
    p = REPO / "runs" / "intent_disagreements.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
