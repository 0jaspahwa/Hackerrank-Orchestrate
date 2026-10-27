"""CSV loading and shared constants. Paths resolve relative to the repository root."""
from __future__ import annotations

import csv
import os
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = Path(os.environ["DATASET_DIR"]) if os.environ.get("DATASET_DIR") else REPO_ROOT / "dataset"

# resource-id prefix -> logical system_id used in output steps
PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "object": "object_storage",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
}
RID_RE = re.compile(r"\b(" + "|".join(sorted(PREFIX_SYSTEM, key=len, reverse=True)) + r")_\d+\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}\d")


def prefix_of(resource_id: str) -> str:
    m = RID_RE.fullmatch(resource_id)
    return m.group(1) if m else resource_id.rsplit("_", 1)[0]


def system_of(resource_id: str) -> str:
    return PREFIX_SYSTEM.get(prefix_of(resource_id), "unknown")


def norm_email(e: str) -> str:
    return e.strip().lower()


def norm_phone(p: str) -> str:
    return re.sub(r"[^\d+]", "", p or "")


def email_hash(e: str) -> str:
    return hashlib.sha256(norm_email(e).encode("utf-8")).hexdigest()


class InputError(ValueError):
    """Input that cannot be processed safely (e.g. duplicate case_id); main.py exits non-zero."""


def parse_ts(ts) -> str | None:
    """ISO-8601-ish timestamp -> canonical 'YYYY-MM-DDTHH:MM:SSZ' (UTC), or None if unparseable."""
    from datetime import datetime, timezone
    t = str(ts or "").strip().replace(" ", "T")
    if not t:
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_requests(rows: list[dict], flags: dict) -> list[dict]:
    """Canonicalize timestamps (valid canonical values are unchanged). An unparseable audit_at falls back
    to reported_at, then requested_at; if no time is usable the request is marked 'unusable' and is
    answered NOT_APPLICABLE for human review (never auto-COMPLETE). Emails are trimmed."""
    for r in rows:
        cid = r["case_id"]
        r["email"] = (r.get("email") or "").strip()
        r["request_text"] = r.get("request_text") or ""
        good = {}
        for f in ("requested_at", "reported_at", "audit_at"):
            n = parse_ts(r.get(f))
            if n is None:
                flags.setdefault(cid, []).append(f"malformed {f} {r.get(f)!r}")
            else:
                good[f] = n
                r[f] = n
        for f in ("requested_at", "reported_at", "audit_at"):
            if f not in good:
                alt = next((good[g] for g in ("reported_at", "requested_at", "audit_at") if g in good), None)
                if alt is None:
                    flags.setdefault(cid, []).append("unusable: no parseable timestamp")
                    r[f] = "1970-01-01T00:00:00Z"        # valid sentinel; the case is answered for review
                else:
                    flags.setdefault(cid, []).append(f"{f} falls back to {alt}")
                    r[f] = alt
    return rows


def read_csv(name: str) -> list[dict]:
    with open(DATASET / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Dataset:
    requests: list[dict]
    samples: list[dict]
    identities: dict[str, dict]
    audit_log: list[dict]
    activity: list[dict]
    rules: list[dict]
    manifest: dict[str, dict]
    sample_output: list[dict] = field(default_factory=list)
    input_flags: dict = field(default_factory=dict)      # case_id -> [input problems handled conservatively]

    @classmethod
    def load(cls) -> "Dataset":
        flags: dict = {}
        requests = normalize_requests(read_csv("deletion_requests.csv"), flags)
        samples = normalize_requests(read_csv("sample_deletion_requests.csv"), flags)             if (DATASET / "sample_deletion_requests.csv").exists() else []
        ids = [r["case_id"] for r in requests + samples]
        dup = sorted({c for c in ids if ids.count(c) > 1})
        if dup:
            raise InputError(f"duplicate case_id in requests: {dup}")
        activity = []
        for a in read_csv("system_activity.csv"):
            n = parse_ts(a.get("event_time"))
            if n is None:
                flags.setdefault("_activity", []).append(f"dropped {a.get('activity_id')}: malformed event_time")
                continue
            a["event_time"] = n
            activity.append(a)
        return cls(
            requests=requests,
            samples=samples,
            identities={r["user_id"]: r for r in read_csv("user_identities.csv")},
            audit_log=read_csv("user_audit_log.csv"),
            activity=activity,
            rules=read_csv("deletion_rules.csv"),
            manifest={r["file_id"]: r for r in read_csv("evidence_files.csv")},
            sample_output=read_csv("sample_output.csv") if (DATASET / "sample_output.csv").exists() else [],
            input_flags=flags,
        )
