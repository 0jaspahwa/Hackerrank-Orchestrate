"""Tie-break signals for account slots of the workflow solver (measured on the hold-out
in code/evaluation/timing_holdout.py before adoption; see docs/rule_ledger.md §D).

A signal sees one open (request, slot) with >= 2 free candidate activities and may
return the one it prefers, or None to abstain.
"""
from __future__ import annotations


class CoLocation:
    """Prefer the candidate account observed in the same evidence files (captured <= audit_at)
    as the request's own pinned or assigned non-account resources. Generators place a case's
    resources - including a deleted account's 404 - in the same export."""
    name = "colocation"

    def __init__(self, ix, pins, requests):
        self.ix, self.pins, self.requests = ix, pins, requests
        self.files_of: dict[str, set] = {}
        for o in ix.obs:
            self.files_of.setdefault(o.resource_id, set()).add(o.file_id)

    def pick(self, solver, key, free, result):
        cid = key[0]
        audit = self.requests[cid]["audit_at"]
        mine = set()
        rids = {r for r in self.pins.get(cid, ()) if not r.startswith("account_")}
        rids |= {a.resource_id for k, a in result.items() if k[0] == cid and not a.resource_id.startswith("account_")}
        for rid in rids:
            mine |= {o.file_id for o in self.ix.by_rid.get(rid, ()) if o.captured_at <= audit}
        if not mine:
            return None
        score = {aid: len(self.files_of.get(solver.actions[aid].resource_id, set()) & mine) for aid in free}
        best = max(score.values())
        winners = [a for a, s in score.items() if s == best]
        return winners[0] if best > 0 and len(winners) == 1 else None


class FollowupSignature:
    """Account tie-break from the workflow's clock (D43 candidate). The first deletion-worker row on one
    of the request's own (identifier-linked) resources after its account submission lands at a fixed
    second-of-minute offset: (follow-up - account) mod 60 is in ALLOWED for 184/184 identifier-pinned
    accounts. A candidate that fits while every other candidate does not is picked."""
    name = "signature"
    ALLOWED = {10, 11, 21, 22, 40, 41, 51, 52}
    WINDOW = 3 * 3600

    def __init__(self, activity, pins, allowed=None):
        from .workflow import _epoch
        if allowed is not None:
            self.ALLOWED = set(allowed)
        self.rows = {}
        for a in activity:
            if a["source_system"] == "deletion_worker" and not a["resource_id"].startswith("account_"):
                self.rows.setdefault(a["resource_id"], []).append(_epoch(a["event_time"]))
        self.pins = pins

    def pick(self, solver, key, free, result):
        cid, slot = key
        if slot[0] != "account":
            return None
        mine = sorted(t for r in self.pins.get(cid, ()) for t in self.rows.get(r, ()))
        if not mine:
            return None
        fits = []
        for aid in free:
            t = solver.actions[aid].t
            nxt = [m for m in mine if 0 < m - t <= self.WINDOW]
            if nxt and round(nxt[0] - t) % 60 in self.ALLOWED:
                fits.append(aid)
        return fits[0] if len(fits) == 1 else None
