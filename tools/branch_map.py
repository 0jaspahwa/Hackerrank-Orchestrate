"""Per-row branch map over the 30 labeled sample rows.

The working surface for designing the decision tree against real cases instead
of in the abstract. One row per labeled example: what it is, what the ground
truth says, and which branch we decided should fire.

BRANCHES is filled in by hand, one row at a time, as each case is adjudicated.
An empty entry prints as TBD - it is not a default or a guess. Nothing here
infers a branch; that is the whole point of the artifact.

  py tools/branch_map.py                 table, all 30 rows
  py tools/branch_map.py --stratified    table, the 5 boundary rows
  py tools/branch_map.py --detail ID...  full context for adjudication
"""

import csv
import sys
import textwrap
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"

# message_id -> (branch_name, one-sentence rationale)
# Filled in during adjudication. Do not populate speculatively.
BRANCHES: dict[str, tuple[str, str]] = {}

# Chosen to stress the hardest boundaries rather than to sample evenly.
STRATIFIED = [
    "sample_msg_003",  # notify   - @mention overrides a heavy-dismisser history
    "sample_msg_007",  # digest   - wanted marketing, opted in, and a benign domain mismatch
    "sample_msg_053",  # mute     - scam, and the dataset's prompt-injection case
    "sample_msg_015",  # mute     - opted-out marketing, the opt-out discriminator
    "sample_msg_004",  # notify   - identical repeat that is NOT muted
]

COLS = [("message_id", 15), ("conv", 9), ("media", 6), ("ground truth", 24), ("branch", 26)]


def _load(name):
    with open(DATASET / f"{name}.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _identical_repeat_ids(samples, history) -> set[str]:
    """Rows whose cited evidence text equals the incoming text byte for byte."""
    out = set()
    for s in samples:
        for e in s["evidence_message_ids"].split(";"):
            e = e.strip()
            if e and e != "none" and history[e]["message_text"].strip() == s["message_text"].strip():
                out.add(s["message_id"])
    return out


def table(rows) -> None:
    header = "  ".join(name.ljust(width) for name, width in COLS)
    print(header)
    print("-" * len(header))
    for row in rows:
        branch, why = BRANCHES.get(row["message_id"], ("- TBD -", ""))
        cells = [
            row["message_id"], row["conversation_type"], row["media_type"] or "-",
            f'{row["action"]}/{row["message_type"]}', branch,
        ]
        print("  ".join(str(c).ljust(w) for c, (_, w) in zip(cells, COLS)))
        for i, line in enumerate(textwrap.wrap(why, 96)):
            print(f"      why: {line}" if i == 0 else f"           {line}")
    print("-" * len(header))
    print(f"{sum(1 for r in rows if r['message_id'] in BRANCHES)}/{len(rows)} rows adjudicated")


def detail(rows) -> None:
    users = {r["user_id"]: r for r in _load("users")}
    groups = {r["group_id"]: r for r in _load("groups")}
    members = {(r["group_id"], r["user_id"]): r for r in _load("group_members")}
    biz = {r["business_id"]: r for r in _load("business_accounts")}
    rel = {(r["user_id"], r["business_id"]): r for r in _load("user_business_history")}
    history = {r["message_id"]: r for r in _load("message_history")}
    events = {(r["user_id"], r["message_id"]): r for r in _load("message_events")}
    identical = _identical_repeat_ids(_load("sample_messages"), history)

    for s in rows:
        print("=" * 96)
        print(f'{s["message_id"]}  ->  GROUND TRUTH: {s["action"]}/{s["message_type"]}  (conf {s["confidence"]})')
        print(f'  recipient {s["user_id"]}  {s["created_at"]}  fwd={s["forwarded_count"]}  media={s["media_type"] or "none"}')

        flags = []
        if s["message_id"] in identical:
            flags.append("BYTE-IDENTICAL REPEAT of its own cited evidence")
        u = users[s["user_id"]]
        print(f'  USER: DND {u["do_not_disturb_window"]} | opened {u["messages_opened_30d"]} '
              f'replied {u["messages_replied_30d"]} dismissed {u["notifications_dismissed_30d"]} '
              f'reported {u["messages_reported_30d"]}')

        if s["group_id"]:
            g, m = groups[s["group_id"]], members[(s["group_id"], s["user_id"])]
            sender = members.get((s["group_id"], s["sender_user_id"]))
            print(f'  GROUP: {g["group_name"]} [{g["group_type"]}] {g["member_count"]} members, {g["messages_30d"]} msgs/30d')
            print(f'  MEMBERSHIP: role={m["role"]} read={m["messages_read_30d"]} replies={m["replies_sent_30d"]} '
                  f'dismissed={m["notifications_dismissed_30d"]} MUTED={m["group_muted_by_user"]}')
            print(f'  SENDER {s["sender_user_id"]}: role={sender["role"] if sender else "NOT A MEMBER"}')
        elif s["sender_user_id"]:
            print(f'  SENDER {s["sender_user_id"]}: direct message, no group context')

        if s["business_id"]:
            b = biz[s["business_id"]]
            match = b["official_domain"] == b["domain_used_by_sender"]
            print(f'  BUSINESS: {b["display_name"]} [{b["category"]}] verified={b["verified"]} '
                  f'acct_age={b["account_age_days"]}d reports30d={b["user_reports_30d"]}')
            print(f'    official={b["official_domain"] or "(none on record)"}  used={b["domain_used_by_sender"]}  '
                  f'used_domain_age={b["domain_used_by_sender_age_days"]}d')
            if not match:
                flags.append(f'DOMAIN MISMATCH ({b["official_domain"] or "no official domain"} -> {b["domain_used_by_sender"]}, '
                             f'{b["domain_used_by_sender_age_days"]}d old, verified={b["verified"]}, {b["user_reports_30d"]} reports)')
            h = rel.get((s["user_id"], s["business_id"]))
            if h:
                opted = h["allows_promotions"] == "1"
                print(f'  RELATIONSHIP: {h["why_user_knows_account"]} | opened30d={h["messages_opened_30d"]} '
                      f'dismissed30d={h["messages_dismissed_30d"]}')
                flags.append(f'OPT-IN STATE: allows_promotions={h["allows_promotions"]}'
                             + ("" if opted else f', opted out {h["promotions_opted_out_at"] or "never explicitly"}'))
            else:
                flags.append("OPT-IN STATE: no relationship row at all - cold contact")
        elif s["conversation_type"] == "business":
            flags.append("business conversation with no business_id")

        for f in flags:
            print(f"  ** {f}")

        print("  TEXT:")
        for line in textwrap.wrap(s["message_text"], 90):
            print("    " + line)
        print(f'  GT reason: {s["reason"]}')
        for e in s["evidence_message_ids"].split(";"):
            e = e.strip()
            if e and e != "none":
                h, k = history[e], events.get((history[e]["user_id"], e), {})
                print(f'  EVIDENCE {e}: opened={k.get("message_opened")} replied={k.get("message_replied")} '
                      f'dismissed={k.get("notification_dismissed")} reported={k.get("message_reported")} '
                      f'muted_after={k.get("muted_after_message")}')
                for line in textwrap.wrap(h["message_text"], 84):
                    print("      | " + line)


def main() -> int:
    args = sys.argv[1:]
    samples = _load("sample_messages")
    by_id = {r["message_id"]: r for r in samples}

    if args and args[0] == "--detail":
        detail([by_id[i] for i in (args[1:] or STRATIFIED)])
    elif args and args[0] == "--stratified":
        table([by_id[i] for i in STRATIFIED])
    else:
        table(samples[: int(args[0])] if args else samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
