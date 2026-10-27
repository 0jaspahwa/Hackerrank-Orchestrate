"""Screenshot extraction: the vision model *describes* the image, code corroborates and decides.

For each PNG:
  1. OCR with rapidocr (repo venv; cached in .cache/ocr/) - optional.
  2. Vision LLM transcription into a fixed JSON schema (cached in .cache/llm/).
  3. Corroboration: every extracted resource id / email / user id / phone must appear in the OCR
     text, or verbatim elsewhere in the participant dataset (CSV rows, logs, JSON, SQL, PDF text).
     Anything else is dropped and logged to runs/vision_dropped.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .client import REPO, complete
from .models import VISION

OCR_CACHE = REPO / ".cache" / "ocr"
# largest prompt-v2 extraction measured at 1129 output tokens (qwen3.8-27b, 11 PNGs); ~33 % headroom.
# Groq reserves max_tokens against the 8000 TPM budget, so 8192 made every call collide with the window.
MAX_TOKENS = 1500
RID_PAT = re.compile(r"\b(?:account|search|support|export|analytics|backup|data_flow|audit_record|tombstone|legal_hold|"
                     r"marketing|object|cache|email_delivery)_\d+\b")
EMAIL_PAT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_PAT = re.compile(r"\buser_\d+\b")

PROMPT = """You are transcribing an internal admin-console screenshot for a data-deletion audit.
Copy every identifier exactly as displayed (ids, emails, phone numbers, dates). Never guess, normalize or invent.
Return one JSON object:
{
 "page_title": str,                               // the large heading of the page
 "page_subtitle": str|null,                      // the small line directly under the heading, copied exactly
 "page_resource_id": str|null,                   // the resource id the page or its detail panel is about, if shown anywhere
 "status_badge": str|null,                       // the pill/badge next to the page title
 "page_subject": {"person_name": str|null, "emails": [str], "user_ids": [str], "phones": [str]},
                                                 // the single customer the whole page is about; empty when the page lists many
 "records": [                                    // every table row, plus the detail-panel entity
   {"resource_id": str|null,                     // e.g. account_5762, search_428, backup_9287, data_flow_1121 exactly as shown
    "label": str|null,                           // the row's main name / file name / reference as shown
    "person_name": str|null,                     // a human name shown for this record, else null
    "emails": [str], "phones": [str], "user_ids": [str],   // user_ids look like user_17
    "status": str|null,                          // status / health / state text for this record
    "fields": {str: str}}                        // other label: value pairs for this record (detail panel included)
 ],
 "all_resource_ids": [str], "all_emails": [str], "all_user_ids": [str], "all_phones": [str]
}
The all_* lists must include identifiers shown ANYWHERE: headings, subtitles, breadcrumbs, table cells, detail panels, activity feeds and the search box."""


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


# ------------------------------------------------------------------ OCR
def ocr_lines(path: Path) -> list[dict] | None:
    """rapidocr lines with geometry: [{"text", "y", "h"}] (y = box centre, h = box height). Cached."""
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    cp = OCR_CACHE / f"{path.stem}_{h}_v2.json"
    snap = Path(__file__).resolve().parent / "cache_snapshot" / "ocr" / cp.name
    if not cp.exists() and snap.exists() and os.environ.get("LLM_NO_SNAPSHOT") != "1":
        cp = snap
    if cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))["lines"]
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    res, _ = RapidOCR()(str(path))
    lines = []
    for box, text, _conf in res or []:
        ys = [p[1] for p in box]
        lines.append({"text": text, "y": round((min(ys) + max(ys)) / 2, 1), "h": round(max(ys) - min(ys), 1)})
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"lines": lines}, ensure_ascii=False), encoding="utf-8")
    return lines


def ocr_text(path: Path) -> str | None:
    lines = ocr_lines(path)
    return None if lines is None else "\n".join(l["text"] for l in lines)


def same_row(lines: list[dict], anchor: str, value: str) -> bool | None:
    """Is `value` on the same OCR row as `anchor`? None when the anchor is not found by OCR."""
    a = [l for l in lines if anchor.lower() in l["text"].lower().replace(" ", "")]
    if not a:
        return None
    v = value.lower().replace(" ", "")
    vd = _digits(value) if not any(c.isalpha() for c in value) else ""
    for al in a:
        for l in lines:
            if abs(l["y"] - al["y"]) <= max(al["h"], l["h"]) * 0.6:
                t = l["text"].lower().replace(" ", "")
                if re.search(r"(?<![\w.@-])" + re.escape(v) + r"(?![\w@-])", t) or (vd and vd in _digits(t)):
                    return True
    return False


# ------------------------------------------------------------------ corpus for corroboration
_CORPUS: str | None = None


def dataset_corpus(ds) -> str:
    """Lower-cased text of every participant file except the images themselves."""
    global _CORPUS
    if _CORPUS is None:
        from audit.data import DATASET
        from audit.evidence import pdf_text
        parts = []
        for p in sorted(DATASET.rglob("*")):
            if not p.is_file() or p.suffix.lower() == ".png" or p.name in ("sample_output.csv", "output.csv"):
                continue
            parts.append(pdf_text(p) if p.suffix.lower() == ".pdf" else p.read_text(encoding="utf-8", errors="ignore"))
        _CORPUS = "\n".join(parts).lower()
    return _CORPUS


class Corroborator:
    def __init__(self, ds, ocr: str | None):
        self.corpus = dataset_corpus(ds)
        self.ocr = (ocr or "").lower()
        self.ocr_compact = re.sub(r"\s+", "", self.ocr)
        self.ocr_digits = _digits(self.ocr)
        self.corpus_digits = None

    def check(self, kind: str, value: str) -> str | None:
        """-> 'ocr' | 'dataset' | None"""
        v = (value or "").strip()
        if not v:
            return None
        if kind == "phone":
            d = _digits(v)
            if len(d) < 7:
                return None
            if self.ocr and d in self.ocr_digits:
                return "ocr"
            if self.corpus_digits is None:
                self.corpus_digits = _digits(self.corpus)
            return "dataset" if d in self.corpus_digits else None
        lv = v.lower()
        bound = r"(?<![\w.@-])" + re.escape(lv) + r"(?![\w@-])"     # whole token: no truncated misreads
        # OCR glues neighbours ("marketing_001-sj.park@..."): a "-" boundary is accepted after a digit only
        glued = r"(?:(?<![\w.@-])|(?<=\d-))" + re.escape(lv.replace(" ", "")) + r"(?![\w@-])"
        if self.ocr and (re.search(bound, self.ocr) or re.search(glued, self.ocr_compact)):
            return "ocr"
        return "dataset" if re.search(bound, self.corpus) else None


# ------------------------------------------------------------------ extraction
def extract(ds, fid: str, path: Path, use_llm: bool = True, models=None) -> dict:
    """-> corroborated extraction (see PROMPT schema) plus provenance; empty dict when nothing usable.
    models: (provider, model) list tried in order; default = pinned model, then the availability fallback."""
    ocr = ocr_text(path)
    raw, model = None, None
    if use_llm:
        for provider, m in (models or VISION):
            try:
                r = complete(provider, m, PROMPT, image=path, max_tokens=MAX_TOKENS, purpose=f"vision:{fid}")
                raw, model = r.json(), f"{provider}:{m}"
                break
            except Exception as e:                       # fall back to the next model
                from . import client as _c
                if _c.STRICT:
                    raise _c.StrictAbort(f"{provider}:{m if 'vision' in __name__ else model} failed: {str(e)[:120]}") from e
                print(f"[vision] {fid} {provider}:{m} failed: {str(e)[:120]}", file=sys.stderr)
    if raw is None:
        return {"file_id": fid, "ocr_available": ocr is not None, "model": None, "records": [], "dropped": []}
    cor = Corroborator(ds, ocr)
    dropped = []

    def keep(kind, vals, where):
        out = []
        for v in vals or []:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if kind == "rid":
                m = RID_PAT.search(v)
                v = m.group(0) if m else ""
            elif kind == "email":
                m = EMAIL_PAT.search(v)
                v = m.group(0).lower() if m else ""
            elif kind == "user":
                m = USER_PAT.search(v)
                v = m.group(0) if m else ""
            src = cor.check("phone" if kind == "phone" else kind, v) if v else None
            if src:
                out.append(v)
            elif v:
                dropped.append({"file_id": fid, "kind": kind, "value": v, "where": where})
        return sorted(set(out), key=out.index)

    subj = raw.get("page_subject") or {}
    page_subject = {"person_name": subj.get("person_name"), "emails": keep("email", subj.get("emails"), "page_subject"),
                    "user_ids": keep("user", subj.get("user_ids"), "page_subject"),
                    "phones": keep("phone", subj.get("phones"), "page_subject")}
    lines = ocr_lines(path) or []
    records = []
    for i, rec in enumerate(raw.get("records") or []):
        if not isinstance(rec, dict):
            continue
        rid = keep("rid", [rec.get("resource_id")] if rec.get("resource_id") else [], f"records[{i}]")
        r = {"resource_id": rid[0] if rid else None, "label": rec.get("label"),
             "person_name": rec.get("person_name"),
             "emails": keep("email", rec.get("emails"), f"records[{i}]"),
             "phones": keep("phone", rec.get("phones"), f"records[{i}]"),
             "user_ids": keep("user", rec.get("user_ids"), f"records[{i}]"),
             "status": rec.get("status"), "fields": rec.get("fields") or {}}
        if r["resource_id"] and lines:            # row-level corroboration: identifiers share the id's OCR row
            for kind in ("emails", "user_ids", "phones"):
                ok = []
                for v in r[kind]:
                    if same_row(lines, r["resource_id"], v) is False:
                        dropped.append({"file_id": fid, "kind": kind, "value": v, "where": f"records[{i}] not on the row of {r['resource_id']}"})
                    else:
                        ok.append(v)
                r[kind] = ok
        records.append(r)
    out = {"file_id": fid, "ocr_available": ocr is not None, "model": model, "ocr_text": ocr or "",
           "fallback_used": model is not None and model != f"{(models or VISION)[0][0]}:{(models or VISION)[0][1]}",
           "page_title": raw.get("page_title"), "page_subtitle": raw.get("page_subtitle"),
           "page_resource_id": (keep("rid", [raw.get("page_resource_id")] if raw.get("page_resource_id") else [],
                                     "page_resource_id") or [None])[0],
           "status_badge": raw.get("status_badge"), "page_subject": page_subject,
           "records": records,
           "all_resource_ids": keep("rid", raw.get("all_resource_ids"), "all_resource_ids"),
           "all_emails": keep("email", raw.get("all_emails"), "all_emails"),
           "all_user_ids": keep("user", raw.get("all_user_ids"), "all_user_ids"),
           "all_phones": keep("phone", raw.get("all_phones"), "all_phones"),
           "dropped": dropped}
    return out


def extract_all(ds, use_llm: bool = True, models=None, log_name: str = "vision_dropped.jsonl") -> dict[str, dict]:
    from audit.data import DATASET
    out, dropped = {}, []
    for fid, m in sorted(ds.manifest.items()):
        if m["file_path"].lower().endswith(".png"):
            e = extract(ds, fid, DATASET / m["file_path"], use_llm, models)
            out[fid] = e
            dropped += e.get("dropped", [])
    log = REPO / "runs" / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in dropped), encoding="utf-8")
    return out
