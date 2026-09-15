"""The output contract for the Message Notification Router.

Single source of truth for the submission format. Every other module derives
its allowed values and column order from here; nothing restates them.

Spec: design/01-problem-analysis.md
  1.2  columns, in order
  1.4  action     - exactly 3 values
  1.5  message_type - exactly 11 values
  E5   confidence in [0, 1]
  E6   evidence is ';'-separated ids, or the literal 'none'
  I3   no cell is ever empty
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_serializer

# Sentinel for "no useful historical message exists" (spec 1.3 / E6).
NO_EVIDENCE = "none"


class Action(StrEnum):
    """Final routing decision. Exactly these 3 values (spec 1.4)."""

    NOTIFY = "notify"  # interrupt the user now
    DIGEST = "digest"  # safe but low priority; show later
    MUTE = "mute"  # repetitive, unwanted, low-value, suspicious, or unsafe


class MessageType(StrEnum):
    """Best-fit message category. Exactly these 11 values, in spec order (1.5)."""

    PERSONAL = "personal"
    URGENT = "urgent"
    EVENT = "event"
    PAYMENT = "payment"
    BUSINESS_UPDATE = "business_update"
    PROMOTION = "promotion"
    GREETING = "greeting"
    FORWARD = "forward"
    SPAM = "spam"
    SCAM = "scam"
    UNKNOWN = "unknown"


class OutputRow(BaseModel):
    """One row of output.csv. Field order IS the column order (E2)."""

    message_id: str = Field(min_length=1)
    action: Action
    message_type: MessageType
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_message_ids: list[str] = Field(default_factory=list)

    @field_serializer("evidence_message_ids")
    def _serialize_evidence(self, ids: list[str]) -> str:
        """Join to the contract cell form, falling back to the 'none' sentinel.

        Keeping this in the serializer means no caller can emit an empty cell.
        """
        return ";".join(ids) if ids else NO_EVIDENCE


# Derived, never hand-written, so the order cannot drift from the model (E2).
COLUMNS: tuple[str, ...] = tuple(OutputRow.model_fields)
