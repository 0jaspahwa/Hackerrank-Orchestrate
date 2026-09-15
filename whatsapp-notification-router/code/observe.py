"""Typed observations over untrusted message text.

The decision layer reads the boolean flags this produces. It never reads the
raw text for these signals. That is the structural guarantee: a message can
lie in its content, but it cannot reach into the branch that judges it.

Two implementations behind one record:
  - deterministic screen (below) - always available, no key, no cost
  - model observation - would fill the same fields; not runnable without
    credits, so every number reported today comes from the screen

A deterministic screen has one property the model path does not: it cannot be
talked out of matching. Adversarial text can defeat a classifier's judgment;
it cannot defeat a regex looking for "OTP".
"""

import re

OTP = [
    r"\bOTP\b", r"one[\s-]?time[\s-]?(password|code|pin)",
    r"\b\d\s*[- ]?digit\b.{0,24}\bcode\b", r"\b(login|verification|security)\s+code\b",
]
CREDENTIALS = [
    r"\bpassword\b", r"\bPIN\b", r"\bcredentials?\b", r"\bcard\s+number\b",
    r"\bCVV\b", r"\bconfirm\s+(your\s+)?(password|pin|card)\b", r"\bKYC\b",
]
URGENCY = [
    r"\bexpires?\b", r"\bexpiring\b", r"\bblocked?\b", r"\bsuspend(ed)?\b",
    r"\bimmediately\b", r"\burgent\b", r"\bwithin\s+\d", r"\b\d+\s*(min|minute|hour|hr)s?\b",
    r"\bact\s+now\b", r"\bverify\s+now\b", r"\blast\s+chance\b",
    r"\b(call|come|reply|respond)\s+(back\s+)?now\b", r"\bescalat(e|ion|ing)\b",
]

# Explicit de-escalation. Without this, "Don't call now, nothing urgent" reads
# as urgent to a keyword screen - the words are all there, the meaning is the
# opposite. Cheap to check, and both labeled voice notes rely on it.
NEGATORS = [
    r"\bnothing\s+urgent\b", r"\bno\s+rush\b", r"\bno\s+pressure\b",
    r"\bdon'?t\s+call\b", r"\bno\s+need\s+to\b", r"\bnot\s+urgent\b",
    r"\bwhenever\s+you\b", r"\bno\s+hurry\b",
]
LINKISH = [r"\b[\w-]{3,}\.(in|com|net|org|co|io|me|pro|xyz)\b", r"https?://"]
CLICK_OR_CALL = [
    r"\bclick\b", r"\btap\b", r"\bdial\b", r"\bcall\s+(us|now|back)\b",
    r"\breply\s+with\b", r"\bopen\s+(the\s+)?link\b", r"\bverify\s+(now|at|your)\b",
    r"\bconfirm\s+(now|your)\b",
]


def _any(patterns, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def observe_text(text: str) -> dict:
    """Facts about a message's text. Never a verdict."""
    text = text or ""
    return {
        "kind": "text",
        "source": "deterministic_screen",
        "asks_for_otp": _any(OTP, text),
        "asks_for_credentials": _any(CREDENTIALS, text),
        "urgency_language": _any(URGENCY, text) and not _any(NEGATORS, text),
        "explicitly_not_urgent": _any(NEGATORS, text),
        "has_suspicious_link": _any(LINKISH, text),
        "asks_to_click_or_call": _any(CLICK_OR_CALL, text),
    }
