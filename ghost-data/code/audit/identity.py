"""Resolve a request email to the subject's stable user_id and every identifier it has used."""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import Dataset, email_hash, norm_email, norm_phone


@dataclass
class Subject:
    user_id: str | None
    emails: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    phones: set = field(default_factory=set)

    @property
    def identifiers(self) -> set:
        ids = self.emails | self.hashes | self.phones
        if self.user_id:
            ids.add(self.user_id)
        return ids


class IdentityResolver:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.by_email: dict[str, str] = {}
        for uid, r in ds.identities.items():
            self.by_email[norm_email(r["email"])] = uid
        self.history: dict[str, list[dict]] = {}
        for a in ds.audit_log:
            self.history.setdefault(a["user_id"], []).append(a)
            if a["field_name"] == "email":
                for v in (a["old_value"], a["new_value"]):
                    self.by_email.setdefault(norm_email(v), a["user_id"])

    def resolve(self, email: str) -> Subject:
        uid = self.by_email.get(norm_email(email))
        if uid is None:
            e = norm_email(email)
            return Subject(None, {e}, {email_hash(e)}, set())
        cur = self.ds.identities[uid]
        s = Subject(uid, {norm_email(cur["email"])}, {cur["email_hash"]}, {norm_phone(cur["phone"])})
        for a in self.history.get(uid, []):
            for v in (a["old_value"], a["new_value"]):
                if not v:
                    continue
                if a["field_name"] == "email":
                    s.emails.add(norm_email(v))
                    s.hashes.add(email_hash(v))
                elif a["field_name"] == "email_hash":
                    s.hashes.add(v.strip())
                elif a["field_name"] == "phone":
                    s.phones.add(norm_phone(v))
        s.emails.add(norm_email(email))
        s.hashes.add(email_hash(email))
        return s
