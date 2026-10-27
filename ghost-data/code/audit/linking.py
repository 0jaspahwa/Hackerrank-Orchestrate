"""Identifier-based links between a request's subject and resources (DECISIONS A3)."""
from __future__ import annotations

import re
from collections import defaultdict

from .data import EMAIL_RE, RID_RE, USER_RE, Dataset, norm_email
from .evidence import EvidenceIndex, Observation
from .identity import IdentityResolver, Subject

LINK_RE = re.compile(r"linked (\S+) \(([^)]*)\) to (\S+) and (user_\d+)|confirmed (\S+) \(([^)]*)\) belongs only to (\S+) and (user_\d+)")


class Linker:
    def __init__(self, ds: Dataset, ix: EvidenceIndex, idr: IdentityResolver):
        self.ds, self.ix, self.idr = ds, ix, idr
        # identifier -> observations carrying it
        self.by_ident: dict[str, list[Observation]] = defaultdict(list)
        for o in ix.obs:
            for i in o.identifiers:
                self.by_ident[i].append(o)
        # catalog links: LINK_RESOURCE_IDENTITY activity -> (resource, ref, email, user_id, time, activity)
        self.catalog: list[dict] = []
        for a in ds.activity:
            if a["event_type"] != "LINK_RESOURCE_IDENTITY":
                continue
            m = LINK_RE.search(a["details"])
            if not m:
                continue
            g = [x for x in m.groups() if x is not None]
            self.catalog.append(dict(resource_id=g[0], ref=g[1], email=norm_email(g[2]), user_id=g[3],
                                     time=a["event_time"], activity_id=a["activity_id"],
                                     system=a["target_system"]))

    def attach_artifacts(self, max_lag_s: int = 15 * 60) -> None:
        """Each catalog LINK follows the capture of exactly one PDF/PNG artifact (7 min later in all 12
        rows). Record that file as an 'artifact' observation of the linked resource so it can be
        cited; its state comes only from the artifact's extracted text (PDF text / image extraction)."""
        from datetime import datetime
        def t(x):
            return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp()
        arts = [(fid, m) for fid, m in self.ds.manifest.items() if m["file_path"].lower().endswith((".png", ".pdf"))]
        for c in self.catalog:
            near = [(t(c["time"]) - t(m["captured_at"]), fid) for fid, m in arts
                    if 0 <= t(c["time"]) - t(m["captured_at"]) <= max_lag_s]
            if not near:
                continue
            fid = min(near)[1]
            c["file_id"] = fid
            m = self.ds.manifest[fid]
            text = self.ix.documents.get(fid, "")
            fields = {"resource_id": c["resource_id"], "catalog_activity": c["activity_id"], "_artifact_text": text}
            if fid in self.ix.images and self.ix.images[fid].get("model"):
                fields["_extraction"] = {**match_record(self.ix.images[fid], c["resource_id"], c["ref"]),
                                         "_ocr": self.ix.images[fid].get("ocr_text", "")}
            self.ix._add(fid, m, c["resource_id"], fields, f"artifact<-{c['activity_id']}", "artifact",
                         link_extra=[c["email"], c["user_id"]])
            o = self.ix.obs[-1]
            self.ix.by_rid.setdefault(o.resource_id, []).append(o)
            for i in o.identifiers:
                self.by_ident[i].append(o)

    def linked_observations(self, subj: Subject) -> list[Observation]:
        seen, out = set(), []
        for i in subj.identifiers:
            for o in self.by_ident.get(i, ()):
                if id(o) not in seen:
                    seen.add(id(o))
                    out.append(o)
        return out

    def catalog_links(self, subj: Subject) -> list[dict]:
        return [c for c in self.catalog if c["user_id"] == subj.user_id or c["email"] in subj.emails]

    def linked_resource_times(self, subj: Subject) -> dict[str, list[tuple]]:
        """resource_id -> [(event_time, captured_at, source)] for every identifier link."""
        out: dict[str, list[tuple]] = defaultdict(list)
        for o in self.linked_observations(subj):
            out[o.resource_id].append((o.event_time, o.captured_at, o.file_id))
            src = o.fields.get("source_resource_id")       # e.g. search doc -> account
            if src and RID_RE.fullmatch(str(src)):
                out[src].append((o.event_time, o.captured_at, o.file_id))
        for c in self.catalog_links(subj):
            out[c["resource_id"]].append((c["time"], c["time"], c["activity_id"]))
        return out


def match_record(ext: dict, rid: str, ref: str) -> dict:
    """The screenshot record a catalog LINK points at: by resource id, by the catalog reference
    (file name, document ref), or - for a page-level reference such as user_17 - the page itself."""
    keys = {rid.lower(), (ref or "").lower()}
    for rec in ext.get("records", []):
        if (rec.get("resource_id") or "").lower() in keys or (rec.get("label") or "").strip().lower() in keys:
            return {**rec, "found": True}
    subj = ext.get("page_subject") or {}
    header = " ".join(str(ext.get(k) or "") for k in ("page_title", "page_subtitle", "page_resource_id")).lower()
    if (ref or "").lower() in {u.lower() for u in subj.get("user_ids", [])} or \
            (ext.get("page_title") or "").strip().lower() in keys or \
            any(re.search(r"\b" + re.escape(k) + r"\b", header) for k in keys if k):
        return {"resource_id": None, "label": ext.get("page_title"), "person_name": subj.get("person_name"),
                "emails": subj.get("emails", []), "phones": subj.get("phones", []), "user_ids": subj.get("user_ids", []),
                "status": ext.get("status_badge"), "fields": {}, "found": True, "page_level": True}
    return {"found": False}
