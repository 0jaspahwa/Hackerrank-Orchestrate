"""Global assignment of deletion-worker activities to requests (DECISIONS D15).

Most deletion-worker rows carry no identifier: once an account is deleted or an
analytics profile anonymized, the only thing tying it to a request is *when* the
workflow touched it relative to that request's reported_at. Many requests share a
reported_at, so the assignment is solved jointly over all 550 requests.

Measured structure (docs/rule_ledger.md §D):
  * account submission  ("submitted primary profile")   reported_at -1.25h .. -0.73h,
    result == reported_status mapped COMPLETE->SUCCESS, PARTIALLY_COMPLETE->PARTIAL,
    NOT_STARTED->FAILED. Exactly one per request (when present).
  * policy actions ("Policy action completed", SUCCESS)  reported_at -0.48h .. -0.05h,
    one chain per request, consecutive actions 390 s / 449 s apart (alternating).
    A request may have zero policy actions.
  * legacy hand-crafted template ("reported the requested operation ...") +-0.3h.
  * post-report failures / CHECK_* rows are NOT timing-assigned: their window spans
    many requests and their resources still carry identifiers.

Accounts are solved request-centrically (each request owns one account), policy actions
activity-centrically (each action belongs to exactly one request, requests may own none).
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .data import prefix_of, system_of

STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
WINDOWS = {"account": (-1.40, -0.50), "policy": (-0.55, -0.03), "legacy": (-0.30, 0.30)}
CHAIN_STEPS = (390, 449)
CHAIN_TOL = 3
ACCOUNT_SLOT = ("account", "DELETE_RECORD", "account_service")


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def hours(a: str, b: str) -> float:
    return (_epoch(a) - _epoch(b)) / 3600


def classify(a: dict) -> str | None:
    d = a["details"]
    if a["source_system"] != "deletion_worker":
        return None
    if "reported the requested operation" in d or "stopped after the target system" in d \
            or "completed some internal work" in d:
        return "legacy"
    if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service":
        return "account"
    if d.startswith("Policy action completed"):
        return "policy"
    return None


def chain_deltas(max_steps: int = 4) -> list[int]:
    out = set()
    for n in range(1, max_steps + 1):
        for first in (0, 1):
            out.add(sum(CHAIN_STEPS[(first + i) % 2] for i in range(n)))
    return sorted(out)


CHAIN_DELTAS = chain_deltas()
# (policy action - the request's account submission) mod 60 s for identifier-pinned actions (64/64):
# the account signature residues {10,11,40,41} shifted by chain steps of 390 / 449 s (= 30 / 29 mod 60)
POLICY_RESIDUES = {9, 10, 11, 39, 40, 41}
SIGNATURE_WINDOW = 3 * 3600
POLICY_PREFIXES = {"analytics", "support", "data_flow", "backup", "legal_hold", "tombstone"}


def _tombstone_conflict(prefix: str, owned: set) -> bool:
    """A tombstone request owns no other policy resource (11/11 linked tombstone requests), and vice versa."""
    others = (owned & POLICY_PREFIXES) - {"tombstone"}
    return (prefix == "tombstone" and bool(others)) or (prefix in POLICY_PREFIXES - {"tombstone"} and "tombstone" in owned)


@dataclass
class WorkerAction:
    activity_id: str
    event_time: str
    t: float
    event_type: str
    resource_id: str
    result: str
    kind: str
    slot: tuple
    candidates: list = field(default_factory=list)


@dataclass
class Assignment:
    request_id: str
    slot: tuple
    activity_id: str
    resource_id: str
    how: str                        # pinned | unique | signal:<name> | tie
    alternatives: list = field(default_factory=list)


class WorkflowSolver:
    policy_pick = "fewest"          # D39: "fewest" for actions still open after chain + signature; "omit" | "pick2" kept for measurement
    chain_mode = "adjacent"         # "all": fits every anchor; "adjacent": one step from each neighbour (D37)
    # ablation switches for the hold-outs (all adopted = True)
    use_chain = True
    use_prefix = True
    use_tombstone = True
    use_policy_signature = True     # D46: (policy action - own account submission) mod 60 in POLICY_RESIDUES

    def __init__(self, requests: list[dict], activity: list[dict]):
        self.requests = {r["case_id"]: r for r in requests}
        rep = sorted((_epoch(r["reported_at"]), r["case_id"]) for r in requests)
        rep_t = [t for t, _ in rep]
        self.actions: dict[str, WorkerAction] = {}
        for a in activity:
            kind = classify(a)
            if kind is None:
                continue
            slot = ACCOUNT_SLOT if kind == "account" else (kind, a["event_type"], system_of(a["resource_id"]))
            t = _epoch(a["event_time"])
            wa = WorkerAction(a["activity_id"], a["event_time"], t, a["event_type"], a["resource_id"],
                              a["reported_result"], kind, slot)
            lo, hi = WINDOWS[kind]
            i = bisect_left(rep_t, t - hi * 3600)
            while i < len(rep) and rep_t[i] <= t - lo * 3600:
                cid = rep[i][1]
                i += 1
                if kind == "account" and STATUS_RESULT.get(self.requests[cid]["reported_status"]) != a["reported_result"]:
                    continue
                wa.candidates.append(cid)
            if wa.candidates:
                self.actions[wa.activity_id] = wa

    # ------------------------------------------------------------------ solve
    def solve(self, pins: dict[str, set], user_of: dict[str, str] | None = None, hide: set = frozenset(),
              signals=(), break_ties: bool = True,
              owners: dict[str, set] | None = None) -> dict[tuple, Assignment]:
        """pins: request -> identifier-linked resource ids. hide: requests whose pins are ignored
        (hold-out). user_of: request -> user; a resource identifier-linked only to other users is
        never a candidate. owners: resource ->
        users whose identifiers appear on it (any of the 850, requester or not); such a resource is
        never timing-assigned to a request of a different user.
        Returns {(request, slot[, activity]): Assignment}."""
        excluded = self._foreign(pins, user_of, hide, owners)
        cands = {aid: [c for c in wa.candidates if (c, aid) not in excluded] for aid, wa in self.actions.items()}
        result: dict[tuple, Assignment] = {}
        owner: dict[str, tuple] = {}

        def assign(cid, aid, how, alts=()):
            wa = self.actions[aid]
            key = (cid, wa.slot) if wa.kind != "policy" else (cid, wa.slot, aid)
            result[key] = Assignment(cid, wa.slot, aid, wa.resource_id, how, sorted(alts))
            owner[aid] = key

        for aid, wa in self.actions.items():
            owners = [c for c in cands[aid] if c not in hide and wa.resource_id in pins.get(c, ())]
            if len(owners) == 1 and (wa.kind == "policy" or (owners[0], wa.slot) not in result):
                assign(owners[0], aid, "pinned")
        self._solve_request_centric(cands, result, owner, assign, signals, break_ties)
        self._solve_policy(cands, result, owner, assign, pins, hide)
        return result

    def _foreign(self, pins, user_of, hide, owners=None):
        if not user_of:
            return set()
        linked_users: dict[str, set] = defaultdict(set)
        for rid, us in (owners or {}).items():
            linked_users[rid] |= set(us)
        for cid, rids in pins.items():
            if cid not in hide:
                for rid in rids:
                    linked_users[rid].add(user_of.get(cid))
        out = set()
        for aid, wa in self.actions.items():
            us = linked_users.get(wa.resource_id)
            if us:
                for c in wa.candidates:
                    if user_of.get(c) not in us:
                        out.add((c, aid))
        return out

    def _solve_request_centric(self, cands, result, owner, assign, signals, break_ties):
        domains: dict[tuple, set] = defaultdict(set)
        for aid, wa in self.actions.items():
            if wa.kind == "policy" or aid in owner:
                continue
            for c in cands[aid]:
                if (c, wa.slot) not in result:
                    domains[(c, wa.slot)].add(aid)
        keys_of: dict[str, set] = defaultdict(set)
        for k, v in domains.items():
            for aid in v:
                keys_of[aid].add(k)
        queue = [k for k, v in domains.items() if len(v) == 1]

        def take(key, aid, how):
            assign(key[0], aid, how, domains[key] - {aid})
            domains[key] = {aid}
            for k in keys_of[aid] - {key}:
                domains[k].discard(aid)
                if k not in result and len(domains[k]) == 1:
                    queue.append(k)

        def propagate():
            while queue:
                k = queue.pop()
                if k not in result and len(domains[k]) == 1:
                    take(k, next(iter(domains[k])), "unique")

        propagate()
        for sig in signals:
            for key in sorted(domains):
                if key in result or len(domains[key]) < 2:
                    continue
                best = sig.pick(self, key, sorted(domains[key]), result)
                if best:
                    take(key, best, "signal:" + sig.name)
                    propagate()
        self.open_accounts = {k: set(v) for k, v in domains.items() if k not in result and v}
        if not break_ties:
            return
        for key in sorted(domains):
            if key in result or not domains[key]:
                continue
            free = sorted(domains[key], key=lambda x: (self.actions[x].event_time, x))
            take(key, free[0], "tie" if len(free) > 1 else "unique")
            propagate()

    def _chain_fits(self, t: float, anchors: list) -> bool:
        if self.chain_mode == "all":
            return all(any(abs(abs(t - at) - d) <= CHAIN_TOL for d in CHAIN_DELTAS) for at in anchors)
        # adjacent: the nearest anchor before and after sit exactly one chain step away (390 / 449 s);
        # farther anchors are not constrained (one measured flow->backup step is 270 s, case_390)
        before = [at for at in anchors if at < t]
        after = [at for at in anchors if at > t]
        near = ([t - max(before)] if before else []) + ([min(after) - t] if after else [])
        return bool(near) and all(any(abs(d - st) <= CHAIN_TOL for st in CHAIN_STEPS) for d in near)

    def _solve_policy(self, cands, result, owner, assign, pins, hide):
        """Each request owns at most one resource per system prefix (32/32 samples, 545/550 linked
        sets): a request that already has a different resource of this prefix is not a candidate."""
        pending = [aid for aid, wa in self.actions.items() if wa.kind == "policy" and aid not in owner]
        has = defaultdict(set)                   # request -> {(prefix, resource)}
        for cid, rids in pins.items():
            if cid not in hide:
                has[cid] |= {(prefix_of(r), r) for r in rids}
        last_cs: dict[str, list] = {}

        def propagate():
            changed = True
            while changed:
                changed = False
                anchors = defaultdict(list)          # request -> times of its owned policy actions
                for key, a in result.items():
                    if self.actions[a.activity_id].kind == "policy":
                        anchors[key[0]].append(self.actions[a.activity_id].t)
                    has[key[0]].add((prefix_of(a.resource_id), a.resource_id))
                for aid in list(pending):
                    wa = self.actions[aid]
                    p = prefix_of(wa.resource_id)
                    cs = [c for c in cands[aid]
                          if not (self.use_prefix and any(q == p and r != wa.resource_id for q, r in has[c]))
                          and not (self.use_tombstone and _tombstone_conflict(p, {q for q, _ in has[c]}))]
                    last_cs[aid] = cs
                    if not cs:
                        pending.remove(aid)
                        continue
                    fits = []
                    for c in (cs if self.use_chain else ()):
                        if anchors.get(c) and self._chain_fits(wa.t, anchors[c]):
                            fits.append(c)
                    sig = []
                    if self.use_policy_signature and len(cs) > 1 and len(fits) != 1:
                        for c in cs:
                            acc = result.get((c, ACCOUNT_SLOT))
                            if acc is not None:
                                d = wa.t - self.actions[acc.activity_id].t
                                if 0 < d <= SIGNATURE_WINDOW and round(d) % 60 in POLICY_RESIDUES:
                                    sig.append(c)
                    if len(cs) == 1:
                        choice, how = cs[0], "unique"
                    elif len(fits) == 1:
                        choice, how = fits[0], "signal:chain"
                    elif len(sig) == 1:
                        choice, how = sig[0], "signal:signature"
                    # (chain elimination - "every other candidate misfits" - was wrong 2/2 on the hold-out; not used)
                    else:
                        continue
                    assign(choice, aid, how, [c for c in cs if c != choice])
                    pending.remove(aid)
                    changed = True

        propagate()
        self.open_policy = {aid: list(last_cs.get(aid, cands[aid])) for aid in pending}
        if self.policy_pick == "fewest":
            # measured option (D36): the candidate owning the fewest other data resources, if unique
            while True:
                best = None
                for a in sorted(pending, key=lambda x: (self.actions[x].t, x)):
                    cs = last_cs.get(a, ())
                    if len(cs) < 2:
                        continue
                    load = {c: len({q for q, _ in has[c]} - {"account", "audit_record"}) for c in cs}
                    m = min(load.values())
                    win = [c for c in cs if load[c] == m]
                    if len(win) == 1:
                        best = (a, win[0], [c for c in cs if c != win[0]])
                        break
                if not best:
                    break
                assign(best[1], best[0], "signal:fewest", best[2])
                pending.remove(best[0])
                propagate()
        if self.policy_pick == "pick2":
            # measured option: deterministically pick when exactly two candidates remain
            while True:
                two = sorted((self.actions[a].t, a) for a in pending if len(last_cs.get(a, ())) == 2)
                if not two:
                    break
                aid = two[0][1]
                cs = sorted(last_cs[aid], key=_natural)
                assign(cs[0], aid, "tie", cs[1:])
                pending.remove(aid)
                propagate()


def _natural(cid: str):
    head, _, num = cid.rpartition("_")
    return (head, int(num)) if num.isdigit() else (cid, 0)
