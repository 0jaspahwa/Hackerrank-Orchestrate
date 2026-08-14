"""Join one incoming message to every piece of context the router can use.

Pure data assembly - no model calls, no decisions, no network.

Join coverage measured against the real dataset:
  group / group_member      63/63 group messages resolve  (never missing)
  business                  30/30 business messages resolve
  user_business_history     19/30 resolve - 11 are absent, and that absence is
                            itself a signal (no prior relationship with sender)
  message_history           every recipient has 3-32 rows, none has zero
  message_events            412/412 history rows have exactly one event

Missing joins return None so callers can tell "no such relationship" apart from
"relationship exists but is empty".
"""

import csv
import json
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"

_tables: dict[str, list[dict]] = {}


def _table(name: str) -> list[dict]:
    """Read a dataset CSV once and keep it in memory."""
    if name not in _tables:
        # utf-8 is explicit: message_text contains non-ASCII and Windows would
        # otherwise default to cp1252 and corrupt it.
        with open(DATASET / f"{name}.csv", encoding="utf-8") as fh:
            _tables[name] = list(csv.DictReader(fh))
    return _tables[name]


def _first(name: str, **match) -> dict | None:
    """First row whose columns all equal the given values, or None."""
    for row in _table(name):
        if all(row[k] == v for k, v in match.items()):
            return row
    return None


def get_context(message_id: str) -> dict:
    """Everything known about one message in dataset/messages.csv.

    Raises KeyError if the message_id does not exist.
    """
    message = _first("messages", message_id=message_id)
    if message is None:
        raise KeyError(f"no such message_id: {message_id}")

    user_id = message["user_id"]
    group_id = message["group_id"]
    business_id = message["business_id"]

    # Attach each history row's event inline - they are 1:1 on (user, message).
    events = {(e["user_id"], e["message_id"]): e for e in _table("message_events")}
    history = [
        {**h, "event": events.get((h["user_id"], h["message_id"]))}
        for h in _table("message_history")
        if h["user_id"] == user_id
    ]
    history.sort(key=lambda h: h["created_at"], reverse=True)  # newest first

    return {
        "message": message,
        "user": _first("users", user_id=user_id),
        "group": _first("groups", group_id=group_id) if group_id else None,
        "group_member": (
            _first("group_members", group_id=group_id, user_id=user_id)
            if group_id
            else None
        ),
        # The sender's own membership - the decision tree needs their role.
        "sender_member": (
            _first("group_members", group_id=group_id, user_id=message["sender_user_id"])
            if group_id and message["sender_user_id"]
            else None
        ),
        "business": (
            _first("business_accounts", business_id=business_id)
            if business_id
            else None
        ),
        "user_business_history": (
            _first("user_business_history", user_id=user_id, business_id=business_id)
            if business_id
            else None
        ),
        "history": history,
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(get_context(sys.argv[1]), indent=2, ensure_ascii=False))
