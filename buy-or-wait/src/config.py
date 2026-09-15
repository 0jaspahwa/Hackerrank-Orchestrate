"""Every tunable in the system.

No other module may contain a magic number in a branch. Construct a `Config` at
the entry point and pass it down explicitly; nothing here is module-level state
that another module reads behind your back.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parent.parent


class Paths(BaseModel):
    """Where the dataset lives and where results go."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_dir: Path = REPO_ROOT / "dataset"
    output_csv: Path = REPO_ROOT / "output.csv"
    images_dir: Path = REPO_ROOT / "dataset" / "media" / "images"
    #: Caches and metrics land here. Never contains a key.
    artifacts_dir: Path = REPO_ROOT / "artifacts"

    @property
    def requests_csv(self) -> Path:
        return self.dataset_dir / "requests.csv"

    @property
    def sample_requests_csv(self) -> Path:
        return self.dataset_dir / "sample_requests.csv"

    @property
    def financial_profiles_csv(self) -> Path:
        return self.dataset_dir / "financial_profiles.csv"

    @property
    def financial_events_csv(self) -> Path:
        return self.dataset_dir / "financial_events.csv"

    @property
    def exchange_rates_csv(self) -> Path:
        return self.dataset_dir / "exchange_rates.csv"

    @property
    def request_payment_options_csv(self) -> Path:
        return self.dataset_dir / "request_payment_options.csv"

    @property
    def messages_csv(self) -> Path:
        return self.dataset_dir / "messages.csv"

    @property
    def images_csv(self) -> Path:
        return self.dataset_dir / "images.csv"

    def image_path(self, image_id: str) -> Path:
        """`image_07` -> `dataset/media/images/image_07.png` (AGENTS.md 6.1)."""
        return self.images_dir / f"{image_id}.png"


class ForecastConfig(BaseModel):
    """The projection window and the recurrence rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Window is [request_date, request_date + horizon_days], anchored, not rolling.
    horizon_days: int = 90

    #: Trailing span of settled history replayed forward as recurring commitments.
    recurrence_lookback_days: int = 30

    #: Recurring items repeat at this cadence.
    recurrence_period_days: int = 30

    #: Day of month most users' salary settles on.
    default_salary_day_of_month: int = 15

    #: Statuses whose rows never affect cash.
    excluded_statuses: frozenset[str] = frozenset({"cancelled", "failed", "unrealized"})

    #: Direction value that never affects cash.
    non_cash_direction: str = "non_cash"

    #: Pending rows count only in this direction; pending credits are ignored.
    pending_counts_for_direction: str = "debit"

    #: Days per month used when spreading an irregular-spending baseline.
    days_per_month: int = 30

    #: `hybrid` builds its recurring core from series seen in >= this many months.
    hybrid_core_min_months: int = 2

    #: RECENCY GUARD. A series may be projected only when its most recent
    #: occurrence is within `recency_cycles * expected_cycle_days` of the
    #: request date. A commitment that already missed its slot is not a
    #: commitment. Applies to income and expense alike. None disables it.
    recency_cycles: float | None = 1.5

    #: Cycle length assumed when a series has too few occurrences to measure one.
    default_cycle_days: int = 30

    #: A recurring NON-FIXED commitment is always projected as its own named
    #: series, never folded into an averaged baseline. A spending change has to
    #: be able to name the thing it changes, and an average has no name.
    flexible_series_min_occurrences: int = 2

    #: The flexibility value meaning "cannot be changed".
    fixed_flexibility: str = "fixed"


class ProjectionConfig(BaseModel):
    """The chosen `ProjectionRule`, as plain values.

    The rule sweep found a PLATEAU, not a winner (CLAUDE.md E). These defaults
    are the simplest member of the tie band that also maximises exact matches on
    `earliest_date_for_full_payment`, and they use the principled recency guard
    rather than the ad-hoc income-stability filter it subsumes. Change them here,
    never in a branch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selector: str = "hybrid"
    amount_mode: str = "median"
    horizon_months: int = 3
    include_request_date: bool = True
    income_mode: str = "all"


class PlanConfig(BaseModel):
    """Thresholds used when building and checking candidate plans."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A partial payment always has exactly this many instalments.
    partial_payment_count: int = 2

    #: Balance must stay at or above the minimum by at least this much for a
    #: plan to count as safe. Absorbs cent-level rounding, nothing more.
    safety_tolerance: Decimal = Decimal("0.01")


class TransferConfig(BaseModel):
    """Internal-transfer detection thresholds.

    FALSIFIED for this dataset and no longer wired into `build_ledger` -- see
    CLAUDE.md D1. Kept so `ledger.detect_internal_transfers` can still be run as
    a data invariant check.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Legs must land within this many days of each other.
    window_days: int = 3

    #: Magnitudes closer than this count as equal and opposite.
    amount_tolerance: Decimal = Decimal("0.01")


class DecisionConfig(BaseModel):
    """Limits and tolerances used when choosing a plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_spending_changes: int = 3

    #: Money differences smaller than this are treated as equal.
    money_epsilon: Decimal = Decimal("0.005")

    #: Decimal places retained for every emitted amount.
    money_places: int = 2

    #: Flexibility values that may never be changed.
    immutable_flexibility: frozenset[str] = frozenset({"fixed"})

    #: Flexibility values permitting a full stop.
    stoppable_flexibility: frozenset[str] = frozenset({"stoppable", "reducible_or_stoppable"})

    #: Flexibility values permitting a reduction.
    reducible_flexibility: frozenset[str] = frozenset({"reducible", "reducible_or_stoppable"})


class FxConfig(BaseModel):
    """Exchange-rate resolution behaviour."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Rates are quoted on this day of month only.
    quote_day_of_month: int = 15

    #: Maximum distance to the nearest quote before the fallback is loud.
    max_quote_gap_days: int = 45

    #: Longest currency path allowed (direct, one pivot, ...).
    max_conversion_hops: int = 3


class ModelConfig(BaseModel):
    """The model provider. Google Gemini, Flash tier.

    Base URL, model name and key variable all live here so the provider stays
    swappable without touching `ocr.py` or `observe.py`.

    The model never returns a verdict, status, method, amount or date that
    reaches the output file. See the architecture invariant in CLAUDE.md.

    The API key is read from the ENVIRONMENT at call time and is never stored on
    this object, never written to a log, a trace, or a usage report, and never
    read from a file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = "google-gemini"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    #: Vision-capable Flash model. Groq has no vision model on this account.
    #: gemini-2.0-flash was retired; the API's own 404 names this replacement.
    vision_model: str = "gemini-3.6-flash"
    #: Text model for message reconciliation. Same family, same tier.
    text_model: str = "gemini-3.6-flash"

    max_output_tokens: int = 2048
    temperature: float = 0.0
    max_retries: int = 3
    #: Seconds between retries; multiplied by the attempt number. BOUNDED --
    #: never an unbounded backoff loop.
    retry_backoff_seconds: float = 2.0
    #: Hard ceiling on any single sleep, including one a Retry-After asks for.
    max_retry_sleep_seconds: float = 65.0

    #: Pause between calls. The free tier rate-limits by requests-per-minute, so
    #: pacing up front is cheaper than absorbing 429s and retrying.
    inter_call_sleep_seconds: float = 4.0
    request_timeout_seconds: float = 120.0

    #: Environment variable holding the API key.
    api_key_env_var: str = "GEMINI_API_KEY"

    #: Credentials to try, in priority order. Same provider, same model, same
    #: code path -- only the credential differs. A second key exists so a
    #: quota-exhausted primary does not strand the run.
    api_key_env_vars: tuple[str, ...] = ("GEMINI_API_KEY", "GEMINI_API_KEY_2")

    def api_key(self) -> str | None:
        """The highest-priority key present in the environment, or None."""
        for name in self.api_key_env_vars:
            value = os.environ.get(name)
            if value:
                return value
        return None

    def credentials(self) -> tuple[tuple[str, str], ...]:
        """(env var name, key) for every credential present, in priority order.

        The NAME is what gets logged; the key itself never is.
        """
        return tuple(
            (name, os.environ[name]) for name in self.api_key_env_vars if os.environ.get(name)
        )

    def is_enabled(self) -> bool:
        """False when no key is present -- every caller degrades deterministically."""
        return bool(self.api_key())


class GroqConfig(BaseModel):
    """Groq, used for the TEXT stage only. Groq has no vision model.

    OpenAI-compatible, so this is a second client function rather than an
    abstraction layer -- `src/groq.py` sits beside `src/gemini.py`.

    The key is read from the ENVIRONMENT at call time and is never stored here,
    logged, cached, or written to the usage report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = "groq"
    base_url: str = "https://api.groq.com/openai/v1"

    #: Pinned exactly. NOT groq/compound or compound-mini: those are agentic
    #: systems with tool access and a lower daily cap, and tool-using autonomy
    #: is the wrong shape for a layer whose only job is to describe what a
    #: message says.
    text_model: str = "openai/gpt-oss-20b"

    #: Config alternative, same non-agentic shape.
    alternative_text_model: str = "qwen/qwen3.6-27b"

    #: gpt-oss-20b emits reasoning tokens before its answer, so a 10-user batch
    #: hit "max completion tokens reached before generating a valid document"
    #: at 2048. Raised, and paired with a smaller batch below.
    max_output_tokens: int = 4096

    #: Groq reads FEWER users per call than Gemini. Two measured reasons: the
    #: 8,000 TPM ceiling, and strict-schema conformance -- a shorter answer is
    #: one this model gets right more often.
    batch_size: int = 5

    temperature: float = 0.0
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    max_retry_sleep_seconds: float = 65.0

    #: MEASURED: the free tier caps this model at 8,000 tokens per MINUTE, and a
    #: batch of ten users costs ~2,400-4,100. Two-second spacing ran ~9x over
    #: the ceiling and spent four calls discovering it. With five-user batches
    #: (~2,200 tokens round trip) 18s holds ~3.3 calls/minute, about 7,300 TPM
    #: against the 8,000 ceiling. The dry-run prints this arithmetic so the
    #: margin is checked rather than assumed.
    inter_call_sleep_seconds: float = 18.0
    request_timeout_seconds: float = 120.0

    api_key_env_var: str = "GROQ_API_KEY"

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env_var)

    def is_enabled(self) -> bool:
        return bool(self.api_key())


class VisionConfig(BaseModel):
    """Image amount extraction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Currencies written with a DOT as the thousands separator and a comma as
    #: the decimal mark (Indonesian). Everything else uses the western
    #: convention; Indian lakh grouping still uses commas, so stripping commas
    #: handles "1,00,000" correctly.
    dot_grouping_currencies: frozenset[str] = frozenset({"IDR"})

    #: Most minor units any extracted amount may carry.
    max_decimal_places: int = 2

    #: What a document PRINTS, mapped to the ISO code the events table uses.
    #: A page says "Rs.", the ledger says INR, and the model is asked to report
    #: what it sees -- so the reconciliation belongs here, not in the prompt.
    currency_aliases: dict[str, str] = {
        "₹": "INR", "RS": "INR", "RS.": "INR", "INR": "INR",
        "RUPEE": "INR", "RUPEES": "INR", "INR.": "INR",
        "RP": "IDR", "RP.": "IDR", "IDR": "IDR", "RUPIAH": "IDR",
        "$": "USD", "US$": "USD", "USD": "USD", "DOLLAR": "USD", "DOLLARS": "USD",
        "€": "EUR", "EUR": "EUR", "EURO": "EUR", "EUROS": "EUR",
        "R": "ZAR", "ZAR": "ZAR", "RAND": "ZAR",
    }

    #: An extracted value must sit within this factor of the median of the
    #: user's other events in the same category. Order-of-magnitude only.
    plausibility_factor: Decimal = Decimal("100")

    #: Cache of extraction results, keyed by event_id.
    cache_filename: str = "ocr_cache.jsonl"

    #: When Tesseract is unavailable the cross-check cannot run. Extraction
    #: continues, and those rows are marked lower-confidence in the trace.
    require_tesseract: bool = False


class ObserveConfig(BaseModel):
    """Message reconciliation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Which provider reads the messages. Vision always stays on Gemini.
    #: "groq" or "gemini".
    provider: str = "groq"

    #: Users per model call. Request count is the scarce resource, not tokens.
    batch_size: int = 10

    #: A message with no digit, no date, and none of these words cannot carry a
    #: number worth reconciling, so it never reaches the model.
    income_keywords: frozenset[str] = frozenset(
        {
            "salary", "payroll", "wage", "income", "payout", "earning", "earnings",
            "pay", "credit", "refund", "bonus", "commission", "stipend", "pension",
            "deposit", "transfer", "payment", "invoice", "bill", "due", "balance",
            "gaji", "penggajian",
        }
    )

    #: Trailing provenance token every message carries -- "Ref EMP-0001",
    #: "Txn ref BAN-0013". It is a digit that means nothing, and leaving it in
    #: made the pre-filter keep all 215 messages.
    reference_token_pattern: str = r"[A-Z]{2,4}-\d{3,6}"

    #: Escape hatch for messages that change a forecast while quoting NO figure.
    #: user_24's "there are no further scheduled payments" is exactly this.
    termination_keywords: frozenset[str] = frozenset(
        {
            "no further", "no longer", "final", "last payment", "ended", "ends",
            "terminated", "discontinued", "stopped", "closed", "cancelled",
            "canceled", "pending", "not yet", "initiated", "withdrawable",
            "has not reached", "will not", "won't be", "no more",
        }
    )

    #: An amended amount must stay within this factor of the series it amends.
    plausibility_factor: Decimal = Decimal("10")

    #: Amendments below this confidence are discarded.
    min_confidence: float = 0.5

    cache_filename: str = "observation_cache.jsonl"


class Config(BaseModel):
    """Everything, assembled once at the entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paths: Paths = Field(default_factory=Paths)
    forecast: ForecastConfig = Field(default_factory=ForecastConfig)
    projection: ProjectionConfig = Field(default_factory=ProjectionConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)
    #: Falsified for this dataset; retained for the data-invariant test only.
    transfer: TransferConfig = Field(default_factory=TransferConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    fx: FxConfig = Field(default_factory=FxConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    observe: ObserveConfig = Field(default_factory=ObserveConfig)


def default_config() -> Config:
    """The configuration used by the CLI. Call it; do not import a singleton."""
    return Config()
