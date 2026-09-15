"""Token and cost accounting for every model call.

`evaluation/usage_report.md` is a graded submission artifact and it is generated
FROM this file's JSONL, never written by hand and never reconstructed after the
fact. So the recorder is wired in from the first call, not added at the end.

A record is appended per call, successful or not. Nothing here ever touches an
API key.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class CallRecord(BaseModel):
    """One model call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: str
    provider: str
    model: str
    #: "ocr" or "observe" -- which stage spent the tokens.
    stage: str
    input_tokens: int = 0
    output_tokens: int = 0
    #: Reported by the provider when prompt caching applies; 0 when it does not.
    cached_tokens: int = 0
    wall_seconds: float = 0.0
    #: How many dataset rows this one call covered (a batch of users, one image).
    units: int = 1
    #: Which credential was used, by ENV VAR NAME. Never the key itself.
    key_label: str = ""
    ok: bool = True
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageRecorder(BaseModel):
    """Appends a `CallRecord` per call to a JSONL file.

    Holds the records in memory too, so a run can summarise itself without
    re-reading the file.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    path: Path
    records: list[CallRecord] = Field(default_factory=list)

    def record(
        self,
        *,
        provider: str,
        model: str,
        stage: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        wall_seconds: float = 0.0,
        units: int = 1,
        key_label: str = "",
        ok: bool = True,
        error: str = "",
    ) -> CallRecord:
        entry = CallRecord(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            provider=provider,
            model=model,
            stage=stage,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            wall_seconds=wall_seconds,
            units=units,
            key_label=key_label,
            ok=ok,
            error=error[:400],
        )
        self.records.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry

    @property
    def calls(self) -> int:
        return len(self.records)

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def load_records(path: Path) -> list[CallRecord]:
    """Read a metrics JSONL back. Used only by the report generator."""
    if not path.exists():
        return []
    records: list[CallRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(CallRecord.model_validate_json(line))
    return records


#: Published Gemini Flash pricing, USD per million tokens. The free tier bills
#: nothing; these figures are what the same traffic WOULD cost on the paid tier
#: and the report says so explicitly rather than implying a charge was incurred.
#: A model absent from this table is reported as "unpriced" rather than being
#: given an invented rate. Add the published figures here to price a new model.
PRICE_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-2.0-flash": (Decimal("0.10"), Decimal("0.40")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini-1.5-flash": (Decimal("0.075"), Decimal("0.30")),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """Paid-tier equivalent cost in USD, or None for an unpriced model."""
    price = PRICE_PER_MILLION.get(model)
    if price is None:
        return None
    per_in, per_out = price
    million = Decimal(1_000_000)
    return (Decimal(input_tokens) / million * per_in) + (
        Decimal(output_tokens) / million * per_out
    )
