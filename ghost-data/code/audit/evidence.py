"""Parse every evidence file into normalized Observation records.

An Observation is one statement "file F saw resource R at time T with fields X".
Two times matter (DECISIONS D9):
  * captured_at - manifest export time; gates citation and primary state.
  * event_time  - the record's own timestamp (log line, inner snapshot, observed_at);
                  gates discovery when the file itself was captured after audit_at.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .data import (DATASET, EMAIL_RE, HASH_RE, PHONE_RE, RID_RE, USER_RE, Dataset,
                   norm_email, norm_phone, system_of)

TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s*(.*)$")
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')
# keys on a *_started log line that name the subject being looked up
LINK_KEYS = ("lookup_key", "match_key", "subject_key", "scan_key", "subject_ref",
             "correlation_key", "owner_key", "subject_reference", "matched_value")


@dataclass
class Observation:
    file_id: str
    captured_at: str          # manifest captured_at
    event_time: str           # record's own time (falls back to captured_at)
    resource_id: str
    system: str
    locator: str              # table#row, json path or line number
    fields: dict              # normalized record fields (identity keys lower-cased emails)
    source_kind: str          # sql | snapshot | export | lookup | log | handover | document
    table: str = ""           # sql table / json collection the record came from
    identifiers: set = field(default_factory=set)


@dataclass
class StoreSnapshot:
    """A complete table/collection of one store at one time (omission proof, D17)."""
    file_id: str
    captured_at: str
    event_time: str
    system: str
    table: str
    resource_ids: set


def extract_identifiers(value) -> set:
    """Emails (lower), 64-hex hashes, user ids and normalized phones found anywhere in value."""
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    out = {norm_email(e) for e in EMAIL_RE.findall(s)}
    out |= set(HASH_RE.findall(s)) | set(USER_RE.findall(s))
    out |= {norm_phone(p) for p in PHONE_RE.findall(s)}
    return out


def norm_ts(ts: str) -> str:
    """Any ISO-8601 UTC timestamp -> 'YYYY-MM-DDTHH:MM:SSZ' so strings compare chronologically."""
    from datetime import datetime, timezone
    t = str(ts).strip().replace(" ", "T")
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return t
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"')
    return v


def _kvs(text: str) -> dict:
    return {k: _unquote(v) for k, v in KV_RE.findall(text)}


def parse_log_line(line: str) -> dict | None:
    """Normalize the six observability line formats into one flat dict."""
    m = TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    d: dict = {}
    if rest.startswith("{"):
        try:
            d.update(json.loads(rest))
        except json.JSONDecodeError:
            return None
    elif rest.startswith("["):        # [LEVEL] [service] [trace] event: k="v"
        mm = re.match(r"\[(\w+)\] \[([\w.-]+)\] \[([\w.-]+)\] (\w+):\s*(.*)$", rest)
        if not mm:
            return None
        d.update(level=mm[1], service=mm[2], trace_id=mm[3], event=mm[4])
        d.update(_kvs(mm[5]))
    elif rest.startswith("|"):        # | LEVEL | service | trace | event | k="v"
        parts = [p.strip() for p in rest.strip("|").split("|", 4)]
        if len(parts) < 5:
            return None
        d.update(level=parts[0], service=parts[1], trace_id=parts[2], event=parts[3])
        d.update(_kvs(parts[4]))
    elif rest.startswith("<"):        # RFC5424: <pri>1 host service trace - - k=v
        mm = re.match(r"<\d+>1 (\S+) (\S+) (\S+) - - (.*)$", rest)
        if not mm:
            return None
        d.update(service=mm[2], trace_id=mm[3])
        d.update(_kvs(mm[4]))
    elif rest.startswith("requestId="):
        mm = re.match(r'requestId=(\S+) level=(\S+) function=(\S+) message="(.*)"$', rest)
        if not mm:
            return None
        d.update(trace_id=mm[1], level=mm[2], service=mm[3])
        d.update(_kvs(mm[4].replace('\\"', '"')))
    else:                              # plain key=value (optionally led by a bare level)
        lead = re.match(r"^(DEBUG|INFO|WARN|ERROR)\s+(.*)$", rest)
        if lead:
            d["level"] = lead[1]
            rest = lead[2]
        d.update(_kvs(rest))
    d["_ts"] = ts
    for k, v in list(d.items()):
        if v in ("null", "None"):
            d[k] = None
    return d


def _clean(rec: dict) -> dict:
    """Lower-case email-ish values; keep everything else."""
    out = {}
    for k, v in rec.items():
        if isinstance(v, str) and EMAIL_RE.fullmatch(v.strip()):
            v = norm_email(v)
        out[k] = v
    return out


class EvidenceIndex:
    def __init__(self, ds: Dataset, image_extractions: dict[str, dict] | None = None):
        self.ds = ds
        self.obs: list[Observation] = []
        self.stores: list[StoreSnapshot] = []
        self.documents: dict[str, str] = {}     # file_id -> extracted text (pdf/png)
        self.images = image_extractions or {}       # file_id -> corroborated vision extraction
        self.load_errors: list[tuple] = []          # (file_id, error): file skipped, run continues
        for fid, m in sorted(ds.manifest.items()):
            path = DATASET / m["file_path"]
            kind = path.suffix.lower()
            n_obs, n_stores = len(self.obs), len(self.stores)
            try:
                self._load_file(fid, m, path, kind)
            except Exception as e:                   # missing, unreadable or corrupted evidence
                del self.obs[n_obs:], self.stores[n_stores:]
                self.documents.pop(fid, None)
                self.load_errors.append((fid, f"{type(e).__name__}: {str(e)[:120]}"))
        self.by_rid: dict[str, list[Observation]] = {}
        for o in self.obs:
            self.by_rid.setdefault(o.resource_id, []).append(o)

    def _load_file(self, fid, m, path, kind):
        if not path.is_file():
            raise FileNotFoundError(str(m["file_path"]))
        if kind == ".sql":
            self._sql(fid, m, path)
        elif kind == ".json":
            self._json(fid, m, path)
        elif kind == ".log":
            self._log(fid, m, path)
        elif kind == ".txt":
            self._txt(fid, m, path)
        elif kind == ".pdf":
            self.documents[fid] = pdf_text(path)
            self._document(fid, m, self.documents[fid])
        elif kind == ".png":
            ext = self.images.get(fid) or {}
            self.documents[fid] = json.dumps(ext, ensure_ascii=False) if ext.get("model") else ""
            if ext.get("model"):
                self._screenshot(fid, m, ext)

    # ------------------------------------------------------------------ helpers
    def _add(self, fid, m, rid, rec, locator, kind, event_time=None, table="", link_extra=()):
        rec = _clean(rec)
        ids = extract_identifiers({k: v for k, v in rec.items() if not k.startswith("_")})
        for v in link_extra:
            if v:
                ids |= extract_identifiers(str(v))
        self.obs.append(Observation(
            file_id=fid, captured_at=norm_ts(m["captured_at"]), event_time=norm_ts(event_time or m["captured_at"]),
            resource_id=rid, system=system_of(rid), locator=locator, fields=rec,
            source_kind=kind, table=table, identifiers=ids))

    # ------------------------------------------------------------------ formats
    def _sql(self, fid, m, path):
        text = path.read_text(encoding="utf-8")
        hm = re.search(r"Captured at:\s*(\S+)", text) or re.search(r"snapshot_completed_at=(\S+)", text)
        ev_time = hm.group(1) if hm else m["captured_at"]
        con = sqlite3.connect(":memory:")
        con.executescript(text)
        tables = [t for (t,) in con.execute("select name from sqlite_master where type='table'")]
        for t in tables:
            if t == "snapshot_scope":
                continue
            cur = con.execute(f'select * from "{t}"')
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur]
            rids = set()
            for i, row in enumerate(rows):
                if "record_json" in row:
                    rec = json.loads(row["record_json"])
                    rec["_origin_system"] = row.get("origin_system")
                    rec["_collection"] = row.get("collection_name")
                else:
                    rec = dict(row)
                rid = rec.get("resource_id")
                if not rid:
                    continue
                rids.add(rid)
                self._add(fid, m, rid, rec, f"{t}#{i + 1}", "sql", ev_time, table=t)
            # complete account-store tables (D17): profile tables, or mixed tables holding accounts
            if t.startswith("customer_profiles") or any(r.startswith("account_") for r in rids):
                self.stores.append(StoreSnapshot(fid, m["captured_at"], norm_ts(ev_time), "account_service", t,
                                                 {r for r in rids if r.startswith("account_")}))
        con.close()

    def _json(self, fid, m, path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        top_time = doc.get("captured_at") or doc.get("exported_at") or m["captured_at"]
        if "snapshots" in doc:                                  # snapshot bundle
            for si, snap in enumerate(doc["snapshots"]):
                t = snap.get("captured_at", top_time)
                store = snap.get("data_store", "")
                coll = snap.get("collection", "")
                rids = set()
                for ri, rec in enumerate(snap.get("records", [])):
                    rid = rec.get("resource_id")
                    if not rid:
                        continue
                    rids.add(rid)
                    r = dict(rec)
                    r["_data_store"] = store
                    # the record's own time wins over the snapshot's (D9: e.g. a lookup row recorded_at
                    # 06-23T06:00 inside a snapshot captured 06-30)
                    rt = rec.get("recorded_at") or rec.get("observed_at") or t
                    self._add(fid, m, rid, r, f"snapshots[{si}].records[{ri}]", "snapshot", rt, table=f"{store}/{coll}")
                if store == "account_service":
                    self.stores.append(StoreSnapshot(fid, m["captured_at"], norm_ts(t), store, coll, rids))
        elif "service_exports" in doc:                          # periodic service export
            for svc, colls in doc["service_exports"].items():
                for coll, recs in colls.items():
                    for ri, rec in enumerate(recs):
                        rid = rec.get("resource_id")
                        if rid:
                            self._add(fid, m, rid, dict(rec), f"service_exports.{svc}.{coll}[{ri}]", "export",
                                      rec.get("observed_at", top_time), table=f"{svc}/{coll}")
        else:                                                   # record lists
            for ri, rec in enumerate(doc.get("records", [])):
                rid = rec.get("resource_id")
                if rid:
                    kind = "lookup" if "record_type" in rec else "export"
                    self._add(fid, m, rid, dict(rec), f"records[{ri}]", kind,
                              rec.get("recorded_at", top_time), table=doc.get("collection", ""))

    def _document(self, fid, m, text):
        """A business document (PDF) observes every resource id it names, with its identifiers."""
        for rid in sorted({mm.group(0) for mm in RID_RE.finditer(text)}):
            self._add(fid, m, rid, {"resource_id": rid, "_document_text": text}, "document", "document",
                      link_extra=[text])

    def _screenshot(self, fid, m, ext):
        """Each corroborated screenshot record with a resource id is an observation.
        A record without identifiers of its own inherits the page subject's. The page subject is only
        set on a single-customer page (the vision prompt leaves it empty on list pages), and a row's own
        identifiers must sit on its OCR row (llm.vision row check), so a decoy row never inherits.
        On a single-customer page, any other corroborated id shown on the page (e.g. "Support:
        CS-8538 · support_9538" in a customer-details panel) is observed as belonging to that subject."""
        subj = ext.get("page_subject") or {}
        subj_ids = subj.get("emails", []) + subj.get("user_ids", []) + subj.get("phones", [])
        seen = set()
        for i, rec in enumerate(ext.get("records", [])):
            rid = rec.get("resource_id")
            if not rid:
                continue
            seen.add(rid)
            own = rec["emails"] + rec["user_ids"] + rec["phones"]
            self._add(fid, m, rid, {"resource_id": rid, "_extraction": {**rec, "found": True, "_ocr": ext.get("ocr_text", "")}},
                      f"records[{i}]", "screenshot", link_extra=own or subj_ids)
        if subj_ids:
            for rid in ext.get("all_resource_ids", []):
                if rid not in seen and RID_RE.fullmatch(rid):
                    rec = {"resource_id": rid, "label": None, "person_name": None, "emails": [], "phones": [],
                           "user_ids": [], "status": None, "fields": {}, "found": True, "page_level": True,
                           "_ocr": ext.get("ocr_text", "")}
                    self._add(fid, m, rid, {"resource_id": rid, "_extraction": rec},
                              "page", "screenshot", link_extra=subj_ids)

    def _txt(self, fid, m, path):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.startswith("- "):
                continue
            p = [s.strip() for s in line[2:].split("|")]
            if len(p) >= 6:
                rec = dict(system=p[1], resource_id=p[2], record_type=p[3], result=p[4], subject_reference=p[5])
                self._add(fid, m, p[2], rec, f"L{i + 1}", "handover", p[0])

    def _log(self, fid, m, path):
        lines = path.read_text(encoding="utf-8").splitlines()
        started: dict[str, dict] = {}
        for i, line in enumerate(lines):
            d = parse_log_line(line)
            if d is None:
                continue
            tr = d.get("trace_id")
            rid = d.get("resource_id")
            if tr and not rid:
                started.setdefault(tr, {}).update({k: v for k, v in d.items() if k in LINK_KEYS or k in ("event", "source")})
                continue
            if not rid or not RID_RE.fullmatch(str(rid)):
                continue
            ctx = started.get(tr, {}) if tr else {}
            rec = dict(d)
            for k, v in ctx.items():
                rec.setdefault("_start_" + k, v)
            kind = "lookup" if "record_type" in d else "log"
            self._add(fid, m, rid, rec, f"L{i + 1}", kind, d["_ts"], table=d.get("service", ""),
                      link_extra=[ctx.get(k) for k in LINK_KEYS])


_PDF_CACHE: dict[Path, str] = {}


def pdf_text(path: Path) -> str:
    if path in _PDF_CACHE:
        return _PDF_CACHE[path]
    text = ""
    exe = shutil.which("pdftotext")
    if exe:
        res = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, text=True, encoding="utf-8")
        text = res.stdout
    if not text.strip():
        try:
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
        except Exception:
            text = ""
    _PDF_CACHE[path] = text
    return text
