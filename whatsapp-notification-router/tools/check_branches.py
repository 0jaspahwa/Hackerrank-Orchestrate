"""Baseline evaluation of the 5 adjudicated branches. Reports, does not fix.

A. Each branch as an independent predicate over all 30 labeled samples.
B. domain-impersonation over the 12 real domain-mismatch rows in messages.csv.
C. Which samples no branch covers.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

from branches import ORDERED_BRANCHES, first_match  # noqa: E402
from observe import observe_text  # noqa: E402

DATASET = Path(__file__).resolve().parent.parent / "dataset"


def load(name):
    with open(DATASET / f"{name}.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


USERS = {r["user_id"]: r for r in load("users")}
GROUPS = {r["group_id"]: r for r in load("groups")}
MEMBERS = {(r["group_id"], r["user_id"]): r for r in load("group_members")}
BIZ = {r["business_id"]: r for r in load("business_accounts")}
REL = {(r["user_id"], r["business_id"]): r for r in load("user_business_history")}
HISTORY = load("message_history")
EVENTS = {(r["user_id"], r["message_id"]): r for r in load("message_events")}


def build_ctx(row: dict) -> dict:
    """Same shape context.get_context returns, but works for sample rows too."""
    uid, gid, bid = row["user_id"], row["group_id"], row["business_id"]
    return {
        "message": row,
        "user": USERS.get(uid),
        "group": GROUPS.get(gid) if gid else None,
        "group_member": MEMBERS.get((gid, uid)) if gid else None,
        "sender_member": MEMBERS.get((gid, row["sender_user_id"])) if gid else None,
        "business": BIZ.get(bid) if bid else None,
        "user_business_history": REL.get((uid, bid)) if bid else None,
        "history": [{**h, "event": EVENTS.get((h["user_id"], h["message_id"]))}
                    for h in HISTORY if h["user_id"] == uid],
        "observation": None,   # vision degraded: no text_found available
        "text_observation": observe_text(row.get("message_text", "")),
    }


# What each branch is MEANT to catch, defined from ground truth only.
def expected(name, row):
    a, t = row["action"], row["message_type"]
    text = (row["message_text"] or "").lower()
    if name == "injection-defense":
        return t == "scam" and ("ignore" in text and "previous" in text)
    if name == "domain-impersonation":
        b = BIZ.get(row["business_id"]) if row["business_id"] else None
        return bool(b) and t == "scam" and b["official_domain"] != b["domain_used_by_sender"]
    if name == "personalized-override":
        return a == "notify" and row["conversation_type"] == "group" and f"@{row['user_id']}" in (row["message_text"] or "")
    if name == "wanted-marketing":
        return a == "digest" and t == "promotion"
    if name == "unwanted-marketing":
        return a == "mute" and t in ("spam", "promotion")
    if name == "p2p-phishing":
        return t == "scam" and not row["business_id"]
    if name == "transactional-business":
        rel = REL.get((row["user_id"], row["business_id"])) if row["business_id"] else None
        return bool(rel) and rel["why_user_knows_account"].startswith(("recent_", "upcoming_", "pending_")) and a == "notify"
    return False


def main() -> int:
    samples = load("sample_messages")
    ctxs = {r["message_id"]: build_ctx(r) for r in samples}
    by_id = {r["message_id"]: r for r in samples}

    print("=" * 78)
    print("CHECK A - each branch as an independent predicate over all 30 samples")
    print("=" * 78)
    for name, predicate, resolve, _ in ORDERED_BRANCHES:
        fired = [m for m in by_id if predicate(ctxs[m])]
        should = [m for m in by_id if expected(name, by_id[m])]
        missed = [m for m in should if m not in fired]
        wrong = []
        for m in fired:
            a, t = resolve(ctxs[m])
            if by_id[m]["action"] != str(a) or by_id[m]["message_type"] != str(t):
                wrong.append((m, f"{a}/{t}"))
        print(f"\n{name}")
        print(f"  fired on ({len(fired)}): {', '.join(fired) or '-'}")
        print(f"  should have fired but did not ({len(missed)}): {', '.join(missed) or '-'}")
        print(f"  fired but label disagrees ({len(wrong)}):" if wrong else "  fired but label disagrees (0): -")
        for m, pred in wrong:
            print(f"      {m}: predicted {pred}  actual {by_id[m]['action']}/{by_id[m]['message_type']}")

    print()
    print("=" * 78)
    print("CHECK B - domain-impersonation over the 12 real mismatch rows")
    print("=" * 78)
    impersonation = ORDERED_BRANCHES[1][1]
    fired = cleared = 0
    for row in load("messages"):
        if not row["business_id"]:
            continue
        b = BIZ[row["business_id"]]
        if b["official_domain"] == b["domain_used_by_sender"]:
            continue
        hit = impersonation(build_ctx(row))
        fired += hit
        cleared += not hit
        print(f"  {row['message_id']:9} {b['display_name']:26} age={b['domain_used_by_sender_age_days']:>5}d "
              f"verified={b['verified']} reports={b['user_reports_30d']:>2} -> {'FIRES' if hit else 'clears'}")
    print(f"\n  fired: {fired}   cleared: {cleared}   (expected 7 fire / 5 clear)")

    print()
    print("=" * 78)
    print("CHECK C/D - coverage, correctness, and uncovered samples")
    print("=" * 78)
    uncovered, act_ok, both_ok, covered = [], 0, 0, []
    for m, row in by_id.items():
        hit = first_match(ctxs[m])
        if hit is None or hit[0] == "fall-through":
            uncovered.append(m)
            if hit is None:
                continue
        if hit[0] == "fall-through":
            continue
        name, a, t, _ = hit
        covered.append((m, name, f"{a}/{t}", f"{row['action']}/{row['message_type']}"))
        act_ok += str(a) == row["action"]
        both_ok += str(a) == row["action"] and str(t) == row["message_type"]
    n = len(covered)
    print(f"  covered by a specific branch: {n}/30    fell through: {len(uncovered)}/30")
    print(f"  action correct of covered      : {act_ok}/{n}")
    print(f"  action+type correct of covered : {both_ok}/{n}")
    print()
    for mid, name, pred, truth in covered:
        print(f"    {'OK ' if pred == truth else 'XX '} {mid:15} {name:23} {pred:26} truth {truth}")
    print()
    from collections import Counter
    dist = Counter(f"{by_id[m]['action']}/{by_id[m]['message_type']}" for m in uncovered)
    for label, n in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"    {label:26} {n}")
    print(f"\n  uncovered ids: {', '.join(sorted(uncovered))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
