"""End-to-end deterministic audit: request -> (deletion_status, steps, trace)."""
from __future__ import annotations

import re
from collections import defaultdict

from . import intent
from .data import Dataset, prefix_of, system_of
from .evidence import EvidenceIndex
from .identity import IdentityResolver
from .linking import Linker
from .policy import ANONYMIZE, BACKUP, DELETE, FLOW, REDACT, RETAIN, UNSCOPED, Policy
from .signals import CoLocation, FollowupSignature
from .state import ResourceState, StateResolver
from .workflow import ACCOUNT_SLOT, WorkflowSolver

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OPS = [  # allowed-operations table order (A16)
    ("REPORT_REMAINING_RECORD", "FINDING", "PRESENT"), ("REPORT_EXPOSED_PII", "FINDING", "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", "FINDING", "ACTIVE"), ("REPORT_RESTORE_RISK", "FINDING", "ACTIVE"),
    ("DISABLE_DATA_FLOW", "REMEDIATION", "DISABLED"), ("BLOCK_RESTORE", "REMEDIATION", "BLOCKED"),
    ("DELETE_RECORD", "REMEDIATION", "ABSENT"), ("REDACT_RECORD", "REMEDIATION", "REDACTED"),
    ("ANONYMIZE_RECORD", "REMEDIATION", "ANONYMIZED"),
    ("VERIFY_RECORD_ABSENT", "VERIFICATION", "ABSENT"), ("VERIFY_RECORD_PRESENT", "VERIFICATION", "PRESENT"),
    ("VERIFY_RECORD_REDACTED", "VERIFICATION", "REDACTED"), ("VERIFY_RECORD_ANONYMIZED", "VERIFICATION", "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", "VERIFICATION", "DISABLED"), ("VERIFY_RESTORE_BLOCKED", "VERIFICATION", "BLOCKED"),
]
OP_INDEX = {op: i for i, (op, _, _) in enumerate(OPS)}
OP_META = {op: (ph, st) for op, ph, st in OPS}
RECREATE_RE = re.compile(r"repopulated downstream resource (\S+?)\.?$")
CLASSIFY_RE = re.compile(r"classified request for (\S+) as non-deletion")

# treatment -> (finding op when non-compliant, remediation op, verification op, non-compliant states)
PLAN = {
    DELETE: ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT", {"PRESENT", "CLEAN"}),
    REDACT: ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED", {"PRESENT"}),
    ANONYMIZE: ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED", {"PRESENT"}),
    RETAIN: (None, None, "VERIFY_RECORD_PRESENT", set()),
    FLOW: ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", {"ENABLED"}),
    BACKUP: ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED", {"RESTORABLE"}),
}


class Auditor:
    use_account_signature = True     # D43 (2-fold CV >= 0.984 on every fold)
    def __init__(self, ds: Dataset | None = None, image_extractions: dict | None = None,
                 intent_llm: dict | None = None, extra_links: dict | None = None):
        self.ds = ds = ds or Dataset.load()
        self.ix = EvidenceIndex(ds, image_extractions)
        self.idr = IdentityResolver(ds)
        self.lk = Linker(ds, self.ix, self.idr)
        self.lk.attach_artifacts()
        self.states = StateResolver(self.ix)
        self.policy = Policy(ds)
        self.requests = ds.samples + ds.requests
        self.R = {r["case_id"]: r for r in self.requests}
        self.subject = {cid: self.idr.resolve(r["email"]) for cid, r in self.R.items()}
        self.user_of = {cid: s.user_id for cid, s in self.subject.items()}
        self.recreations = [a for a in ds.activity if RECREATE_RE.search(a["details"])]
        self.dw_by_rid = defaultdict(list)
        for a in ds.activity:
            if a["source_system"] == "deletion_worker":
                self.dw_by_rid[a["resource_id"]].append(a)
        self.classified = {CLASSIFY_RE.search(a["details"]).group(1): a["activity_id"]
                           for a in ds.activity if a["event_type"] == "CLASSIFY_REQUEST" and CLASSIFY_RE.search(a["details"])}
        self.intent = {cid: intent.classify(r["request_text"]) for cid, r in self.R.items()}
        self.intent_llm = intent_llm or {}          # model's label per case (confirmation only)
        self.links, self.excluded = {}, {}
        for cid in self.R:
            self.links[cid], self.excluded[cid] = self._identifier_scope(cid)
        # accepted investigator facts enter the same decision layer as every other identifier link
        for cid, extra in (extra_links or {}).items():
            for rid, why in extra.items():
                self.links.setdefault(cid, {}).setdefault(rid, list(why))
        # global workflow solve (D15) over every deletion request
        pins = {cid: set(v) for cid, v in self.links.items()}
        unusable = {c for c, fl in ds.input_flags.items() if any(f.startswith("unusable") for f in fl)}
        self.solver = WorkflowSolver([r for r in self.requests if self.intent[r["case_id"]][0]
                                      and r["case_id"] not in unusable], ds.activity)
        self.owners = self._resource_owners()
        self.assignments = self.solver.solve(pins, user_of=self.user_of, signals=((FollowupSignature(ds.activity, pins),) if self.use_account_signature else ())
                                             + (CoLocation(self.ix, pins, self.R),),
                                             owners=self.owners)
        self.open_accounts = dict(self.solver.open_accounts)
        self.by_case = defaultdict(list)
        for key, a in self.assignments.items():
            self.by_case[key[0]].append(a)

    # ------------------------------------------------------------------ scope
    def _resource_owners(self) -> dict[str, set]:
        """resource -> users whose identifiers (any of the 850, requester or not) appear on it."""
        uid_of = {}
        for uid, row in self.ds.identities.items():
            s = self.idr.resolve(row["email"])
            for i in s.identifiers:
                uid_of.setdefault(i, set()).add(uid)
        out = defaultdict(set)
        for o in self.ix.obs:
            for i in o.identifiers:
                out[o.resource_id] |= uid_of.get(i, set())
        for c in self.lk.catalog:
            out[c["resource_id"]].add(c["user_id"])
        return out

    def _identifier_scope(self, cid):
        r, s = self.R[cid], self.subject[cid]
        req, audit = r["requested_at"], r["audit_at"]
        links = self.lk.linked_resource_times(s)
        prior = []
        prior += [f"earlier request {x}" for x, y in self.R.items()
                  if x != cid and self.user_of[x] == s.user_id and y["audit_at"] < req]
        dw = {a["resource_id"]: a for a in self.ds.activity
              if a["source_system"] == "deletion_worker" and a["event_time"] < req and a["resource_id"] in links}
        prior += [f"earlier deletion-worker {a['activity_id']}" for a in dw.values()]
        prior += [f"earlier catalog link {c['activity_id']}" for c in self.lk.catalog_links(s) if c["time"] < req]
        keep, drop = {}, {}
        for rid, v in links.items():
            if prefix_of(rid) in UNSCOPED:
                continue
            disc = sorted({(e, src) for e, _, src in v if e <= audit})
            if not disc:
                continue
            if prior and all(e < req for e, _ in disc):
                drop[rid] = {"links": disc, "prior_episode": prior[:3]}
                continue
            keep[rid] = [f"identifier link via {src} at {e}" for e, src in disc][:4]
        return keep, drop

    def _weak_duplicates(self, scope: dict) -> list:
        """D24 applied to scope (D34): when a request has several resources of one prefix, a resource
        whose only identifier links are rows of a list-page screenshot is dropped in favour of the
        ones with a stronger link (catalog LINK, structured evidence)."""
        def screenshot_only(rid):
            obs = [o for o in self.ix.by_rid.get(rid, ()) if o.identifiers]
            return bool(obs) and all(o.source_kind == "screenshot" for o in obs)
        by_prefix = defaultdict(list)
        for rid in scope:
            by_prefix[prefix_of(rid)].append(rid)
        out = []
        for rids in by_prefix.values():
            strong = [r for r in rids if not screenshot_only(r)]
            if strong:
                out += [r for r in rids if r not in strong]
        return sorted(out)

    # ------------------------------------------------------------------ audit one case
    def audit(self, cid: str) -> dict:
        r = self.R[cid]
        audit_at = r["audit_at"]
        ok, why = self.intent[cid]
        trace = {"case_id": cid, "email": r["email"], "user_id": self.user_of[cid],
                 "reported_status": r["reported_status"], "requested_at": r["requested_at"],
                 "reported_at": r["reported_at"], "audit_at": audit_at,
                 "intent": {"deletion": ok, "reason": why}, "flags": list(self.ds.input_flags.get(cid, []))}
        if any(f.startswith("unusable") for f in trace["flags"]):
            # no usable time: the audit cannot be anchored, send to human review (never auto-COMPLETE)
            trace["review"] = {"code": "UNUSABLE_TIMESTAMPS", "missing": "no parseable requested_at / reported_at / audit_at"}
            return {"case_id": cid, "deletion_status": "NOT_APPLICABLE", "steps": [], "trace": trace}
        classify_acts = [aid for acc, aid in self.classified.items()
                         if any(s in self.subject[cid].identifiers for o in self.ix.by_rid.get(acc, ()) for s in o.identifiers)]
        llm = self.intent_llm.get(cid)
        if llm:
            trace["intent"]["llm"] = llm
            if llm.get("deletion") is not None and llm["deletion"] != ok:
                trace["flags"].append(f"intent disagreement: rules={ok} llm={llm['deletion']} ({llm['reason']})")
        if classify_acts:
            trace["intent"]["classify_request"] = classify_acts
            if ok:
                trace["flags"].append("intent rule says deletion but CLASSIFY_REQUEST says non-deletion")
        if not ok:
            return {"case_id": cid, "deletion_status": "NOT_APPLICABLE", "steps": [], "trace": trace}
        if self.user_of[cid] is None:
            # the request email matches no known user (current or historical): nothing can be verified as
            # theirs, and timing alone must not attribute records to an unknown person -> human review
            trace["flags"].append("requester identity not found: human review")
            trace["review"] = {"code": "UNRESOLVED_REQUESTER",
                               "missing": f"no user has current or historical email {r['email']!r}"}
            return {"case_id": cid, "deletion_status": "NOT_APPLICABLE", "steps": [], "trace": trace}

        scope = {rid: list(v) for rid, v in self.links[cid].items()}
        weak = self._weak_duplicates(scope)
        for rid in weak:
            trace.setdefault("excluded_weak_link", {})[rid] = scope.pop(rid)
        trace["excluded_prior_episode"] = self.excluded[cid]
        wf = []
        for a in sorted(self.by_case.get(cid, ()), key=lambda a: a.activity_id):
            if prefix_of(a.resource_id) in UNSCOPED:
                continue
            scope.setdefault(a.resource_id, []).append(f"workflow {a.how} {a.activity_id}")
            wf.append({"activity": a.activity_id, "resource": a.resource_id, "slot": "/".join(a.slot), "how": a.how,
                       "alternatives": [self.solver.actions[x].resource_id for x in a.alternatives][:8]
                       if a.alternatives and a.alternatives[0] in self.solver.actions else a.alternatives[:8]})
            if a.how == "tie":
                trace["flags"].append(f"account tie broken deterministically: {a.resource_id}")
        trace["workflow"] = wf
        if (cid, ACCOUNT_SLOT) in self.open_accounts:
            trace["open_account_candidates"] = sorted(self.solver.actions[x].resource_id
                                                      for x in self.open_accounts[(cid, ACCOUNT_SLOT)])

        states = {rid: self.states.at(rid, audit_at) for rid in scope}
        # explicit recreation (D10)
        for act in self.recreations:
            if act["event_time"] > audit_at:
                continue
            src, dst = act["resource_id"], RECREATE_RE.search(act["details"]).group(1)
            if src not in scope and dst not in scope:
                continue
            src_state = states.get(src) or self.states.at(src, audit_at)
            valid = act["event_type"] == "RESTORE_BACKUP" or src_state.state == "ENABLED"
            dst_state = states.get(dst) or self.states.at(dst, audit_at)
            last = max((o.event_time for o in dst_state.basis), default="")
            if valid and act["event_time"] > last:
                for x, why_ in ((src, f"recreation source {act['activity_id']}"), (dst, f"recreated by {act['activity_id']}")):
                    scope.setdefault(x, []).append(why_)
                states[src] = src_state
                dst_state.state, dst_state.recreated_by = "PRESENT", act["activity_id"]
                states[dst] = dst_state
        if not any(prefix_of(x) == "account" for x in scope):
            trace["flags"].append("no account resource linked")

        findings, remediation, verification, res_trace = [], [], [], {}
        for rid in sorted(scope):
            st: ResourceState = states[rid]
            cat, cat_why = self.policy.category(rid, self.ix.by_rid.get(rid, ()))
            tr = self.policy.treatment(cat)
            f_op, r_op, v_op, bad = PLAN[tr]
            sysid = system_of(rid)
            noncompliant = st.state in bad
            cite = st.recreated_by if (noncompliant and st.recreated_by) else st.cite
            if not cite and noncompliant:
                # D47: a finding whose only observation is a post-capture file (D9) must not cite an
                # omission store (it proves the opposite); cite the latest pre-audit deletion-worker
                # activity on the resource instead (Q4 activity fallback)
                acts = [a for a in self.dw_by_rid.get(rid, ()) if a["event_time"] <= audit_at]
                cite = max(acts, key=lambda a: (a["event_time"], a["activity_id"]))["activity_id"] if acts else ""
            elif not cite and prefix_of(rid) == "account":
                cite = self.states.omission_proof(rid, audit_at)
            res_trace[rid] = {"system": sysid, "category": cat, "category_reason": cat_why, "treatment": tr,
                              "state": st.state, "post_capture_only": st.post_capture_only, "cite": cite,
                              "basis": [f"{o.file_id}:{o.locator}@{o.event_time}" for o in st.basis][:4],
                              "linked_by": scope[rid][:4]}
            img = self.ix.images.get(cite) if cite else None
            if img and img.get("model"):
                res_trace[rid]["image_extraction"] = {"file": cite, "model": img["model"],
                                                      "fallback_used": img.get("fallback_used", False)}
                if img.get("fallback_used"):
                    trace["flags"].append(f"screenshot {cite} extracted by fallback model {img['model']}")
            if st.state is None and any(o.source_kind == "artifact" for o in self.ix.by_rid.get(rid, ())):
                trace["flags"].append(f"state unknown: only evidence is an unread artifact ({cite})")
            if noncompliant:
                findings.append(self._step(f_op, sysid, rid, cite))
                remediation.append(self._step(r_op, sysid, rid, ""))
                if not cite:
                    trace["flags"].append(f"finding without citable evidence: {rid}")
            verification.append((v_op, sysid, rid, cite))
        status = "INCOMPLETE" if findings else "COMPLETE"
        steps = findings + remediation
        for v_op, sysid, rid, cite in verification:
            ev = cite if status == "COMPLETE" else ""
            if status == "COMPLETE" and not ev:
                trace["flags"].append(f"COMPLETE verification without citable evidence: {rid}")
            steps.append(self._step(v_op, sysid, rid, ev))
        steps.sort(key=lambda s: (PHASES.index(s["phase"]), OP_INDEX[s["operation"]], s["system_id"], s["resource_id"]))
        for i, s in enumerate(steps, 1):
            s["step_no"] = i
        if status == "COMPLETE" and any(not s["evidence_id"] for s in steps):
            # a COMPLETE answer must prove every verification; when proof is missing (unreadable or absent
            # evidence) the case is never auto-COMPLETE: no finding exists either, so send to human review
            trace["flags"].append("COMPLETE not provable (verification without evidence): human review")
            trace["review"] = {"code": "UNPROVABLE_COMPLETE", "missing": "evidence for " + ", ".join(
                sorted(s["resource_id"] for s in steps if not s["evidence_id"]))}
            status, steps = "NOT_APPLICABLE", []
        if not steps:
            # nothing can be tied to the requester (e.g. an email matching no user and no record): the
            # contract allows no empty deletion row, so answer NOT_APPLICABLE and flag for human review
            # rather than claim a COMPLETE deletion of unknown data
            trace["flags"].append("deletion case with no in-scope resources: human review")
            trace.setdefault("review", {"code": "NOTHING_LINKED",
                                        "missing": "no resource linked to the requester at audit_at"})
            status = "NOT_APPLICABLE"
        trace["resources"] = res_trace
        return {"case_id": cid, "deletion_status": status, "steps": steps, "trace": trace}

    @staticmethod
    def _step(op, sysid, rid, ev):
        ph, st = OP_META[op]
        return {"step_no": 0, "phase": ph, "system_id": sysid, "resource_id": rid, "operation": op,
                "state": st, "evidence_id": ev}
