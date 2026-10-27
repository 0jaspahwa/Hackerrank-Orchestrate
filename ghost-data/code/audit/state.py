"""State of one resource at a case's audit_at, reconstructed from observations (D9, D11, A6-A8, A11).

Each observation yields a verdict:
    ABSENT      the record does not exist (404, rows=0, NOT_FOUND, empty hit list)
    PRESENT     the record exists and still exposes direct identifiers (or exposure is unknown)
    CLEAN       the record exists with identifiers removed (REDACTED / ANONYMIZED / nulls)
    ENABLED / DISABLED       data flow
    RESTORABLE / BLOCKED     backup archive
    None        the line carries no state (e.g. a *_started lookup line)
Claims (activity results, reported_status) never produce a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import EMAIL_RE, PHONE_RE
from .evidence import EvidenceIndex, Observation

TRUE = {"true", "1", "yes", "y", "t"}
FALSE = {"false", "0", "no", "n", "f"}
IDENT_FIELDS = {
    "support_platform": ("requester_email", "requester_name", "requester_phone", "matched_value"),
    "analytics_platform": ("email", "phone", "email_hash", "user_id", "matched_value"),
    "identity_service": ("name", "email", "phone", "email_hash"),
}
# worst-first: the surviving / exposed copy wins a tie (D11)
SEVERITY = {"PRESENT": 3, "CLEAN": 2, "ABSENT": 1, "ENABLED": 2, "DISABLED": 1, "RESTORABLE": 2, "BLOCKED": 1}


def _b(v) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return True if s in TRUE else False if s in FALSE else None


def _absent_marker(f: dict) -> bool:
    return (str(f.get("rows")) == "0" or str(f.get("http_status")) == "404" or str(f.get("status_code")) == "404"
            or str(f.get("result")).upper() == "NOT_FOUND" or str(f.get("total_hits")) == "0")


def verdict(o: Observation) -> str | None:
    f = o.fields
    if "_extraction" in f:
        return screenshot_verdict(o.system, f["_extraction"])
    if o.source_kind == "artifact" and not str(f.get("_artifact_text") or "").strip():
        return None                     # unread screenshot: cited, but state unknown
    ev = str(f.get("event") or "")
    if ev.endswith("_started") or ev in ("thumbnail_failed", "deletion_marker_check"):
        return None
    sysid = o.system
    if sysid == "integration_service":
        res = str(f.get("result") or "").upper()
        en = _b(f.get("enabled"))
        if res == "ACTIVE" or en is True:
            return "ENABLED"
        if res == "DISABLED" or en is False:
            return "DISABLED"
        return None
    if sysid == "backup_service":
        res = str(f.get("result") or "").upper()
        ra = _b(f.get("restore_allowed"))
        if res == "ACTIVE" or ra is True or (_b(f.get("eligible")) is True and _b(f.get("deletion_block")) is False):
            return "RESTORABLE"
        if res == "BLOCKED" or ra is False or _b(f.get("deletion_block")) is True:
            return "BLOCKED"
        return None
    if _absent_marker(f):
        return "ABSENT"
    if "profile" in f and f["profile"] is None:
        return "ABSENT"
    if "hits" in f and isinstance(f["hits"], dict) and not f["hits"].get("documents"):
        return "ABSENT"
    res = str(f.get("result") or "").upper()
    if res in ("REDACTED", "ANONYMIZED"):
        return "CLEAN"
    if res in ("IDENTIFIABLE",):
        return "PRESENT"
    if sysid in IDENT_FIELDS:
        if ev in ("message_pii_scan", "attachment_pii_scan", "contact_lookup_completed"):
            return "PRESENT"
        if "direct_identifiers" in f:
            return "PRESENT" if str(f["direct_identifiers"]) not in ("0", "None") else "CLEAN"
        keys = [k for k in IDENT_FIELDS[sysid] if k in f]
        has_msg = "message_text" in f
        if keys or has_msg:
            # identity fields still filled, or a message body that was not redacted, means exposed;
            # a record holding only the stable reference plus "[REDACTED]" / null text is clean
            exposed = any(f.get(k) not in (None, "", "[REDACTED]") for k in keys)
            msg = f.get("message_text")
            if has_msg and not keys and msg not in (None, "", "[REDACTED]"):
                exposed = True
            if isinstance(msg, str) and (EMAIL_RE.search(msg) or PHONE_RE.search(msg)):
                exposed = True
            return "PRESENT" if exposed else "CLEAN"
    return "PRESENT"


REMOVED_WORDS = ("deleted", "not found", "removed", "purged", "expired")
DISABLED_WORDS = ("disabled", "paused", "stopped", "inactive", "suspended")
BLOCKED_WORDS = ("blocked", "restore disabled", "restore locked", "no restore", "locked")
REDACTABLE = {"support_platform", "analytics_platform", "identity_service"}


def screenshot_verdict(system: str, rec: dict) -> str | None:
    """State shown by a corroborated screenshot record (code decides; the model only transcribed)."""
    if not rec.get("found"):
        return None
    text = " ".join([str(rec.get("status") or "")] + [f"{k} {v}" for k, v in (rec.get("fields") or {}).items()]).lower()
    ocr = str(rec.get("_ocr") or "").lower()

    def seen(words):
        # a compliant state word only counts if the OCR also reads it on the image: the model's text alone
        # (e.g. an instruction injected into the screenshot) can never make a record look compliant
        return any(w in text and w in ocr for w in words)
    if system == "integration_service":
        return "DISABLED" if seen(DISABLED_WORDS) else "ENABLED"
    if system == "backup_service":
        return "BLOCKED" if seen(BLOCKED_WORDS) else "RESTORABLE"
    if seen(REMOVED_WORDS) and system != "identity_service":
        return "ABSENT"
    if system in REDACTABLE:
        exposed = bool(rec.get("emails") or rec.get("phones") or rec.get("person_name"))
        return "PRESENT" if exposed else "CLEAN"
    return "PRESENT"


@dataclass
class ResourceState:
    resource_id: str
    state: str | None                 # verdict at audit_at, None = never observed
    cite: str = ""                    # latest file captured <= audit_at observing the resource
    basis: list = field(default_factory=list)   # observations that decided the state
    post_capture_only: bool = False   # state came from a file captured after audit_at (D9)
    recreated_by: str = ""            # activity id of an explicit recreation (D10)


class StateResolver:
    def __init__(self, ix: EvidenceIndex):
        self.ix = ix

    def at(self, rid: str, audit_at: str) -> ResourceState:
        obs = [o for o in self.ix.by_rid.get(rid, ()) if o.event_time <= audit_at]
        primary = [o for o in obs if o.captured_at <= audit_at]
        cite_pool = [o for o in self.ix.by_rid.get(rid, ()) if o.captured_at <= audit_at]
        cite = max(cite_pool, key=lambda o: (o.captured_at, o.file_id)).file_id if cite_pool else ""
        pool = [o for o in primary if verdict(o)] or [o for o in obs if verdict(o)]
        if not pool:
            return ResourceState(rid, None, cite)
        latest = max(o.event_time for o in pool)
        top = [o for o in pool if o.event_time == latest]
        state = max((verdict(o) for o in top), key=lambda v: SEVERITY[v])
        return ResourceState(rid, state, cite, top, post_capture_only=not any(o.captured_at <= audit_at for o in pool))

    def omission_proof(self, rid: str, audit_at: str) -> str:
        """Latest full account-store snapshot captured <= audit_at that lacks rid (D7/D17)."""
        best = None
        for s in self.ix.stores:
            # inventory captures list a subset of customers, so they never prove absence: gold case_04
            # cites evidence_54 (failover store, 09-02) although inventory evidence_90 (09-05) lacks it too
            if "inventory" in self.ix.ds.manifest[s.file_id]["origin"]:
                continue
            if s.captured_at <= audit_at and s.event_time <= audit_at and rid not in s.resource_ids \
                    and (s.table.startswith("customer_profiles") or s.system == "account_service" and not s.table.startswith("customer_data")):
                if best is None or (s.captured_at, s.file_id) > (best.captured_at, best.file_id):
                    best = s
        return best.file_id if best else ""
