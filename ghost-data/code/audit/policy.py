"""Data category -> required treatment (deletion_rules.csv, D3/D20).

Category precedence (D20): explicit record_type -> record nature -> the deletion worker's
attempted operation on that resource -> the system default.
"""
from __future__ import annotations

from .data import Dataset, prefix_of

# treatment classes used by step emission
DELETE, REDACT, ANONYMIZE, RETAIN, FLOW, BACKUP = "DELETE", "REDACT", "ANONYMIZE", "RETAIN", "FLOW", "BACKUP"

PREFIX_CATEGORY = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "export": "USER_EXPORT",
    "support": "SUPPORT_MESSAGE", "analytics": "ANALYTICS_PROFILE", "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD", "backup": "BACKUP_ARCHIVE", "tombstone": "ACCOUNT_TOMBSTONE",
    "marketing": "MARKETING_PROFILE", "object": "SUPPORT_ATTACHMENT", "data_flow": "DATA_FLOW",
}
# resources that can never be linked or observed (cache entries, delivery logs): no step
UNSCOPED = {"cache", "email_delivery"}
OP_CATEGORY = {"DELETE_RECORD": {"support": "SUPPORT_ATTACHMENT", "analytics": "SECONDARY_IDENTIFIER"},
               "REDACT_RECORD": {"support": "SUPPORT_MESSAGE"},
               "ANONYMIZE_RECORD": {"analytics": "ANALYTICS_PROFILE"}}


class Policy:
    def __init__(self, ds: Dataset):
        self.action = {r["data_category"]: r["required_action"] for r in ds.rules}
        self.fallbacks: set = set()          # (category, unknown action) handled as DELETE
        self.worker_ops: dict[str, set] = {}
        for a in ds.activity:
            if a["source_system"] == "deletion_worker" and a["event_type"] in OP_CATEGORY:
                self.worker_ops.setdefault(a["resource_id"], set()).add(a["event_type"])

    def category(self, rid: str, observations) -> tuple[str, str]:
        """-> (category, reason)"""
        p = prefix_of(rid)
        if p not in ("support", "analytics"):
            return PREFIX_CATEGORY.get(p, "UNKNOWN"), "system default"
        for o in observations:
            rt = o.fields.get("record_type")
            if rt in ("SUPPORT_ATTACHMENT", "SUPPORT_MESSAGE", "ANALYTICS_PROFILE", "SECONDARY_IDENTIFIER",
                      "MARKETING_PROFILE"):
                return rt, f"record_type in {o.file_id}"
        for o in observations:
            f, ev = o.fields, str(o.fields.get("event") or "")
            if p == "support" and (ev == "attachment_pii_scan" or "uploaded_by" in f or "file_name" in f
                                   or "attachment" in o.table or o.source_kind == "document"):
                return "SUPPORT_ATTACHMENT", f"attachment record in {o.file_id}"
            # message_excerpt is NOT NULL in every inventory support_records row (attachments included),
            # so it is not evidence of a message; message_text / message_pii_scan are
            if p == "support" and (ev == "message_pii_scan" or "message_text" in f):
                return "SUPPORT_MESSAGE", f"conversation message in {o.file_id}"
            if p == "analytics" and (ev.startswith("contact_lookup") or "email_opt_in" in f):
                return "SECONDARY_IDENTIFIER", f"contact lookup keyed by identifier in {o.file_id}"
        ops = self.worker_ops.get(rid, set())
        for op in ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"):
            if op in ops and p in OP_CATEGORY[op]:
                return OP_CATEGORY[op][p], f"deletion worker attempted {op}"
        return PREFIX_CATEGORY[p], "system default"

    def treatment(self, category: str) -> str:
        """Required treatment. A category missing from deletion_rules.csv, or an action this pipeline does
        not know, falls back to DELETE (the strictest end state: anything still present is reported, so a
        rules gap can never produce an automatic COMPLETE) and is recorded in self.fallbacks."""
        if category == "DATA_FLOW":
            return FLOW
        if category == "BACKUP_ARCHIVE":
            return BACKUP
        if category == "ACCOUNT_TOMBSTONE":
            return REDACT
        action = self.action.get(category)
        known = {"DELETE": DELETE, "REDACT": REDACT, "ANONYMIZE": ANONYMIZE, "RETAIN": RETAIN}
        if action not in known:
            self.fallbacks.add((category, action))
            return DELETE
        return known[action]
