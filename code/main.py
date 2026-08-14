"""Pipeline orchestration.

INCOMPLETE. Only the degraded-media path is implemented. The decision tree for
rows we CAN see is still being adjudicated case-by-case in tools/branch_map.py
and is deliberately absent - there is no fallthrough here that silently routes
a normal row.

The degraded path exists because the vision seam may be unavailable at run
time (no credits, bad key, network). When that happens the row must still
produce a valid OutputRow: the contract is all-or-nothing across 110 rows, so
one unreadable image cannot be allowed to fail the submission.
"""

import anthropic

from branches import first_match
from context import get_context
from media import observe_image, transcribe_voice
from observe import observe_text
from schema import Action, MessageType, OutputRow

# Ceiling for any row routed without its media content. A decision made blind
# to 'what the image says' must not present as confidently as one that saw it.
DEGRADED_CONFIDENCE_CAP = 0.65

DEGRADED_IMAGE = {
    "kind": "image",
    "legible": False,
    "reason_unavailable": "vision unavailable at build time",
}

# A domain mismatch alone is not scam - Thrillophilia uses link.wame.pro, a
# 3368-day-old domain on a verified account, and is labeled digest. The risky
# population is mismatch AND a young domain AND unverified.
YOUNG_DOMAIN_DAYS = 60


def observe_media(message: dict) -> dict | None:
    """Observation for a row's media, or None if it has none. Never raises.

    Catches anthropic.AnthropicError (the base of every SDK exception,
    including APIConnectionError) and, beyond the spec, ValueError/OSError
    from the media layer itself - img_020.jpg is an AVIF file that raises
    ValueError before any request is made, and a crash there would fail the
    whole run just as surely as a billing error.
    """
    if message["media_type"] != "image":
        return None
    path = f"dataset/media/images/{message['media_id']}.jpg"
    try:
        return observe_image(path)
    except anthropic.AnthropicError as exc:
        return {**DEGRADED_IMAGE, "error": f"{type(exc).__name__}"}
    except (ValueError, OSError) as exc:
        return {**DEGRADED_IMAGE, "error": f"{type(exc).__name__}"}


def _evidence(ctx: dict, limit: int = 2) -> list[str]:
    """Same-sender or same-business history, newest first. Never invents ids."""
    msg = ctx["message"]
    same = [
        h for h in ctx["history"]
        if (msg["business_id"] and h["business_id"] == msg["business_id"])
        or (msg["sender_user_id"] and h["sender_user_id"] == msg["sender_user_id"])
        or (msg["group_id"] and h["group_id"] == msg["group_id"])
    ]
    return [h["message_id"] for h in same[:limit]]


def route_degraded_image(ctx: dict) -> OutputRow:
    """Route an image row whose content could not be read.

    Uses only signals that survive the degradation: sender metadata, business
    relationship, domain check, forwarded_count, conversation type, and the
    user's history with this sender. Never emits notify - claiming a message
    is worth an interruption on evidence that excludes its actual content is
    not a call this layer is entitled to make.
    """
    msg, biz, rel = ctx["message"], ctx["business"], ctx["user_business_history"]
    forwarded = int(msg["forwarded_count"])

    if biz:
        mismatch = biz["official_domain"] != biz["domain_used_by_sender"]
        young = int(biz["domain_used_by_sender_age_days"]) < YOUNG_DOMAIN_DAYS
        if mismatch and young and biz["verified"] == "0":
            return _row(msg, Action.MUTE, MessageType.SCAM, 0.65,
                        "Sender uses an unverified lookalike domain registered recently; "
                        "image content unavailable but sender signals are sufficient.",
                        _evidence(ctx))
        if rel and rel["allows_promotions"] == "0" and rel["promotions_opted_out_at"]:
            return _row(msg, Action.MUTE, MessageType.PROMOTION, 0.6,
                        "Image content unavailable; user has explicitly opted out of "
                        "marketing from this business.", _evidence(ctx))
        if rel is None:
            return _row(msg, Action.DIGEST, MessageType.UNKNOWN, 0.5,
                        "Image content unavailable and the user has no prior relationship "
                        "with this business; deferred rather than interrupting.",
                        _evidence(ctx))
        return _row(msg, Action.DIGEST, MessageType.BUSINESS_UPDATE, 0.55,
                    "Image content unavailable; routed on sender history and business "
                    "relationship.", _evidence(ctx))

    if forwarded >= 5:
        return _row(msg, Action.DIGEST, MessageType.FORWARD, 0.55,
                    "Image content unavailable; heavily forwarded group image deferred "
                    "on forwarding history alone.", _evidence(ctx))

    return _row(msg, Action.DIGEST, MessageType.UNKNOWN, 0.5,
                "Image content unavailable; routed on conversation type and sender "
                "history without the image itself.", _evidence(ctx))


def _row(msg, action, mtype, confidence, reason, evidence) -> OutputRow:
    return OutputRow(
        message_id=msg["message_id"],
        action=action,
        message_type=mtype,
        reason=reason,
        confidence=min(confidence, DEGRADED_CONFIDENCE_CAP),
        evidence_message_ids=evidence,
    )


REASONS = {
    "injection-defense": "The message tries to instruct the router; the decision is based on its actual content and risk.",
    "domain-impersonation": "Sender uses an unverified lookalike domain registered recently, so the message is suppressed as impersonation.",
    "p2p-phishing": "An unreciprocated sender is asking for one-time codes or credentials, which is treated as phishing.",
    "personalized-override": "A group admin directly mentioned the user with a time-bound request that should interrupt now.",
    "wanted-marketing": "The message is promotional but comes from a business the user has opted into and previously opened.",
    "transactional-business": "A verified business is following up on an order or booking the user actually started.",
    "unwanted-marketing": "The user has opted out of or repeatedly dismissed marketing from this business.",
    "group-admin-broadcast": "A group admin sent an operational update that the user is likely to need.",
    "group-direct-mention": "A group member mentioned the user directly and is asking for a personal reply.",
    "muted-or-chain": "Low-value forwarded or repetitive content in a group the user has already turned away from.",
    "peer-listing": "A member is listing an item for sale in a group that exists for buying and selling.",
    "business-informational": "A verified business the user engages with sent a non-promotional update.",
    "ordinary-conversation": "Ordinary conversation with no urgency or risk signals; shown later rather than interrupting.",
    "fall-through": "No branch matched with confidence, so the message is deferred rather than interrupted or suppressed.",
}

# Which slice of history each branch's decision actually rests on.
IDENTICAL_TEXT_BRANCHES = {"wanted-marketing", "unwanted-marketing"}
SENDER_BRANCHES = {"p2p-phishing", "injection-defense", "group-direct-mention", "ordinary-conversation"}
BUSINESS_BRANCHES = {"domain-impersonation", "transactional-business", "business-informational"}


def _evidence_for(branch: str, ctx: dict, limit: int = 2) -> list[str]:
    """History the branch's own reasoning used. 'none' when there is nothing."""
    msg = ctx["message"]
    if branch == "fall-through":
        return []
    body = (msg.get("message_text") or "").strip()
    if branch in IDENTICAL_TEXT_BRANCHES and body:
        hits = [h["message_id"] for h in ctx["history"] if h["message_text"].strip() == body]
        if hits:
            return hits[:limit]
    if branch in BUSINESS_BRANCHES and msg["business_id"]:
        pool = [h for h in ctx["history"] if h["business_id"] == msg["business_id"]]
    elif branch in SENDER_BRANCHES and msg["sender_user_id"]:
        pool = [h for h in ctx["history"] if h["sender_user_id"] == msg["sender_user_id"]]
    else:
        pool = [h for h in ctx["history"]
                if (msg["group_id"] and h["group_id"] == msg["group_id"])
                or (msg["business_id"] and h["business_id"] == msg["business_id"])
                or (msg["sender_user_id"] and h["sender_user_id"] == msg["sender_user_id"])]
    return [h["message_id"] for h in pool[:limit]]


def attach_observations(ctx: dict) -> dict:
    """Add media and text observations to an already-joined context.

    Split out from build_context so the sample-row scorer can exercise this
    exact path rather than a reimplementation of it.
    """
    msg = ctx["message"]
    ctx["observation"] = observe_media(msg)

    # STEP 2: a voice note's transcript is just text. It goes through the same
    # deterministic screen as any other message, and lands in the same
    # text_found slot the tree already reads - so TIME_BOUND, greeting and
    # promotional markers apply to spoken content exactly as to typed content.
    if msg["media_type"] == "voice":
        ctx["observation"] = transcribe_voice_safe(msg)
    body = (msg.get("message_text") or "") or (ctx["observation"] or {}).get("text_found", "")
    ctx["text_observation"] = observe_text(body)
    return ctx


def transcribe_voice_safe(message: dict) -> dict:
    """Voice observation, degrading like the image path rather than crashing."""
    path = f"dataset/media/audio/{message['media_id']}.mp3"
    try:
        result = transcribe_voice(path)
        return {"kind": "voice", "legible": result["legible"],
                "text_found": result["transcript"], "duration_seconds": result["duration_seconds"]}
    except (ValueError, OSError, RuntimeError) as exc:
        return {"kind": "voice", "legible": False, "text_found": "",
                "reason_unavailable": "transcription unavailable", "error": type(exc).__name__}


def build_context(message_id: str) -> dict:
    """Joined context plus observations, ready for the decision tree."""
    return attach_observations(get_context(message_id))


# Branches whose verdict actually consumes image content - they read _text(),
# which folds in the observation's text_found, or the flags derived from it.
# Everything else decides on metadata (group role, mute state, forward count,
# domain age) and is unaffected by a blind image, so short-circuiting those to
# the degraded router threw away correct answers for nothing.
IMAGE_DEPENDENT_BRANCHES = {
    "wanted-marketing",
    "transactional-business",
    "unwanted-marketing",
    "business-informational",
    "ordinary-conversation",
    "fall-through",
}


def route_ctx(ctx: dict) -> OutputRow:
    """Route an already-built context. The single decision path.

    The tree runs FIRST, even for degraded images. Only if the branch that
    fired needs image content do we fall back to the metadata-only router.
    """
    observation = ctx.get("observation") or {}
    degraded_image = observation.get("kind") == "image" and not observation.get("legible", True)

    branch, action, mtype, confidence = first_match(ctx)
    if degraded_image and branch in IMAGE_DEPENDENT_BRANCHES:
        return route_degraded_image(ctx)
    return OutputRow(
        message_id=ctx["message"]["message_id"],
        action=action,
        message_type=mtype,
        reason=REASONS[branch],
        confidence=confidence,
        evidence_message_ids=_evidence_for(branch, ctx),
    )


def route(message_id: str) -> OutputRow:
    """Route one message to a single OutputRow. Never raises for a valid id."""
    return route_ctx(build_context(message_id))
