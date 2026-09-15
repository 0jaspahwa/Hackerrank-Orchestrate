"""End to end: read the requests, decide each one, write `output.csv`.

The only place `Config` is constructed and passed down.

Two guarantees, because a 250-row batch must not be lost to one bad row:
  * every request produces exactly one output row, in INPUT ORDER;
  * any row that raises degrades to `contract.safe_default_row` and is recorded
    in the trace with the exception, never emitted blank and never fatal.

A JSONL trace is written alongside the output: one object per request with the
decision, the binding constraint, the eligible options, the spending changes and
any fallback reason. That trace is how a recommendation gets audited.
"""

from __future__ import annotations

import csv
import json
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from src.capacity import amount_safe_to_pay, earliest_date_for_full_payment
from src.changes import candidate_changes
from src.config import Config, default_config
from src.contract import OutputRow, safe_default_row, to_money, write_output_csv
from src.explain import explain
from src.fx import load_rates
from src.ledger import (
    AmountMode,
    FinancialEvent,
    IncomeMode,
    ProjectionRule,
    build_ledger,
    load_events,
    load_profiles,
)
from src.observe import (
    IncomeAmendment,
    ObservationResult,
    apply_amendment,
    load_messages,
    observe_messages,
)
from src.ocr import (
    ExtractedAmount,
    extract_all,
    load_image_links,
    revalidate,
    usable_amounts,
)
from src.options import eligible_options, load_payment_options
from src.plans import Decision, RequestRow, decide
from src.usage import UsageRecorder, estimate_cost


class RunSummary(BaseModel):
    """What happened across the batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int
    fallbacks: tuple[str, ...] = ()
    status_counts: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    rule_counts: dict[int, int] = {}
    loud_notes: tuple[str, ...] = ()
    missing_amount_rows: tuple[str, ...] = ()
    fx_fallback_rows: tuple[str, ...] = ()

    # -- model layer -------------------------------------------------------
    model_enabled: bool = False
    ocr_extracted: int = 0
    ocr_failed: int = 0
    amendments_applied: int = 0
    amendments_discarded: int = 0
    prefilter_kept: int = 0
    prefilter_skipped: int = 0
    observe_batches: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_notes: tuple[str, ...] = ()


def build_projection_rule(config: Config) -> ProjectionRule:
    """Construct the rule from config. The tunables live in `ProjectionConfig`."""
    return ProjectionRule(
        selector=config.projection.selector,
        amount_mode=AmountMode(config.projection.amount_mode),
        horizon_months=config.projection.horizon_months,
        include_request_date=config.projection.include_request_date,
        income_mode=IncomeMode(config.projection.income_mode),
    )


def load_requests(path: str | Path) -> tuple[RequestRow, ...]:
    """Read `requests.csv` (or `sample_requests.csv`) preserving file order."""
    rows: list[RequestRow] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            completion = (raw.get("desired_completion_date") or "").strip()
            rows.append(
                RequestRow(
                    request_id=raw["request_id"],
                    user_id=raw["user_id"],
                    request_date=date.fromisoformat(raw["request_date"].strip()),
                    request_type=raw.get("request_type", ""),
                    requested_amount=to_money(raw["requested_amount"]),
                    desired_completion_date=date.fromisoformat(completion) if completion else None,
                    allows_partial_payment=(raw.get("allows_partial_payment") or "")
                    .strip()
                    .lower()
                    == "true",
                    request_text=raw.get("request_text", ""),
                )
            )
    return tuple(rows)


class Dataset(BaseModel):
    """Everything loaded once, then reused for every request."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    profiles: dict
    events_by_user: dict
    rates: object
    options_by_request: dict
    #: event_id -> verified amount recovered from an image. Empty without a key.
    image_amounts: dict = {}
    #: user_id -> validated income amendment. Empty without a key.
    amendments: dict = {}
    ocr_results: dict = {}
    observation: object = None


def load_dataset(
    config: Config,
    *,
    recorder: UsageRecorder | None = None,
    use_model: bool = False,
) -> Dataset:
    """Read every input file once, and optionally run the model layer.

    `use_model` is opt-in and, when the API key is absent, silently does nothing:
    `image_amounts` and `amendments` stay empty and every row falls back to the
    deterministic path. That is the degraded mode, not an error.
    """
    events_by_user: dict[str, list[FinancialEvent]] = {}
    for event in load_events(config.paths.financial_events_csv):
        events_by_user.setdefault(event.user_id, []).append(event)
    indexed = {user: tuple(rows) for user, rows in events_by_user.items()}

    image_amounts: dict[str, object] = {}
    amendments: dict[str, IncomeAmendment] = {}
    ocr_results: dict[str, ExtractedAmount] = {}
    observation: ObservationResult | None = None

    # A populated cache is replayable with NO key: cached results are data, not
    # calls. That is what lets the key stay in the operator's own shell while
    # scoring and analysis run here.
    if use_model and recorder is not None:
        ocr_results = extract_all(
            indexed,
            load_image_links(config.paths.images_csv),
            config=config,
            recorder=recorder,
        )
        # Replay the checks over cached readings before using them. Costs
        # nothing and recovers any rejection that a since-fixed validator bug
        # caused -- without which a validator fix needs fresh quota.
        ocr_results = revalidate(ocr_results, indexed, vision=config.vision)
        image_amounts = dict(usable_amounts(ocr_results))

        observation = observe_messages(
            load_messages(config.paths.messages_csv),
            indexed,
            config=config,
            recorder=recorder,
        )
        amendments = dict(observation.amendments)

    return Dataset(
        profiles=load_profiles(config.paths.financial_profiles_csv),
        events_by_user=indexed,
        rates=load_rates(config.paths.exchange_rates_csv),
        options_by_request=load_payment_options(config.paths.request_payment_options_csv),
        image_amounts=image_amounts,
        amendments=amendments,
        ocr_results=ocr_results,
        observation=observation,
    )


def decide_request(
    request: RequestRow,
    dataset: Dataset,
    config: Config,
    rule: ProjectionRule,
) -> tuple[Decision, object]:
    """Run one request through the whole pipeline. Returns the decision + ledger."""
    profile = dataset.profiles[request.user_id]
    events = dataset.events_by_user.get(request.user_id, ())

    ledger = build_ledger(
        request.user_id,
        request.request_date,
        profile,
        events,
        dataset.rates,
        dataset.image_amounts,
        rule,
        forecast=config.forecast,
        fx=config.fx,
    )

    # A validated message amendment corrects our GUESS about what recurs. It
    # runs BEFORE capacity, because capacity must measure the corrected ledger.
    ledger = apply_amendment(ledger, dataset.amendments.get(request.user_id))

    # Capacity, on the BASELINE ledger, before any spending change is considered.
    amount_safe = amount_safe_to_pay(
        ledger, profile.minimum_balance_to_keep, request.requested_amount
    )
    earliest = earliest_date_for_full_payment(
        ledger, profile.minimum_balance_to_keep, request.requested_amount, ledger.window_end
    )

    options = eligible_options(
        dataset.options_by_request.get(request.request_id, ()),
        profile,
        days_per_month=config.forecast.days_per_month,
    )
    changes = candidate_changes(profile, events, config.decision)

    decision = decide(
        request,
        profile,
        ledger,
        options,
        changes,
        amount_safe,
        earliest,
        decision=config.decision,
        plan_config=config.plan,
    )
    return decision, ledger


def to_output_row(request: RequestRow, decision: Decision) -> OutputRow:
    """Render a decision into a contract-validated output row."""
    row = OutputRow(
        request_id=decision.request_id,
        amount_safe_to_pay=decision.amount_safe_to_pay,
        affordability_status=decision.affordability_status,
        recommended_payment_method=decision.recommended_payment_method,
        payment_plan=decision.payment_plan,
        earliest_date_for_full_payment=decision.earliest_date_for_full_payment,
        spending_changes_needed=decision.spending_changes,
        decision_explanation=explain(decision),
        request_date=request.request_date,
        requested_amount=request.requested_amount,
    )
    row.validate_contract()
    return row


def _trace_entry(request: RequestRow, decision: Decision | None, ledger, error: str | None) -> dict:
    if decision is None:
        return {"request_id": request.request_id, "fallback": True, "error": error}
    return {
        "request_id": decision.request_id,
        "user_id": request.user_id,
        "request_date": request.request_date.isoformat(),
        "requested_amount": str(decision.requested_amount),
        "currency": decision.home_currency,
        "amount_safe_to_pay": str(decision.amount_safe_to_pay),
        "affordability_status": decision.affordability_status.value,
        "recommended_payment_method": decision.recommended_payment_method.value,
        "payment_plan": decision.payment_plan.serialise(),
        "earliest_date_for_full_payment": (
            decision.earliest_date_for_full_payment.isoformat()
            if decision.earliest_date_for_full_payment
            else ""
        ),
        "spending_changes_needed": decision.spending_changes.serialise(),
        "status_rule": decision.status_rule,
        "minimum_balance": str(decision.minimum_balance),
        "trough_balance": str(decision.trough_balance),
        "trough_date": decision.trough_date.isoformat() if decision.trough_date else "",
        "desired_completion_date": (
            decision.desired_completion_date.isoformat()
            if decision.desired_completion_date
            else ""
        ),
        "eligible_option_ids": list(decision.eligible_option_ids),
        "methods_considered": list(decision.considered_methods),
        "ledger_flows": len(ledger.flows) if ledger else 0,
        "missing_amounts": list(ledger.missing_amounts) if ledger else [],
        "fx_fallbacks": len(ledger.fx_fallbacks) if ledger else 0,
        "notes": list(decision.notes),
        "fallback": False,
    }


def _guard_image_resolution(config: Config, target: Path, resolved_now: int) -> None:
    """Refuse to overwrite `output.csv` with FEWER resolved image amounts.

    This exists because it already happened: a harness called `run()` without
    `use_model` and silently replaced a model-backed output with a
    deterministic one, discarding every recovered amount. A high-water mark
    beside the artifacts makes that failure loud instead of invisible.

    Only guards the canonical output; a scratch path is free to be anything.
    """
    if target.resolve() != config.paths.output_csv.resolve():
        return

    mark_path = config.paths.artifacts_dir / "resolved_image_amounts.json"
    previous = 0
    if mark_path.exists():
        try:
            previous = int(json.loads(mark_path.read_text(encoding="utf-8"))["resolved"])
        except (ValueError, KeyError, TypeError):
            previous = 0

    if resolved_now < previous:
        raise RuntimeError(
            f"refusing to write {target.name}: it would carry {resolved_now} resolved "
            f"image amounts, down from {previous}. Re-run with use_model=True so the "
            f"OCR cache is replayed, or delete {mark_path.name} if the drop is intended."
        )

    mark_path.parent.mkdir(parents=True, exist_ok=True)
    mark_path.write_text(
        json.dumps({"resolved": max(resolved_now, previous)}, indent=1), encoding="utf-8"
    )


def run(
    config: Config | None = None,
    *,
    requests_path: str | Path | None = None,
    output_path: str | Path | None = None,
    trace_path: str | Path | None = None,
    use_model: bool = False,
    metrics_path: str | Path | None = None,
) -> tuple[tuple[OutputRow, ...], RunSummary]:
    """Decide every request and write `output.csv` plus a JSONL trace.

    `use_model=True` turns on the OCR and message layers. With no API key
    present they do nothing and every row takes the deterministic path.
    """
    cfg = config or default_config()
    rule = build_projection_rule(cfg)
    recorder = UsageRecorder(
        path=Path(metrics_path)
        if metrics_path
        else cfg.paths.artifacts_dir / "usage_metrics.jsonl"
    )
    dataset = load_dataset(cfg, recorder=recorder, use_model=use_model)
    requests = load_requests(requests_path or cfg.paths.requests_csv)

    rows: list[OutputRow] = []
    traces: list[dict] = []
    fallbacks: list[str] = []
    loud: list[str] = []
    missing_rows: list[str] = []
    fx_rows: list[str] = []
    status_counts: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    rule_counts: dict[int, int] = {}

    for request in requests:
        try:
            decision, ledger = decide_request(request, dataset, cfg, rule)
            rows.append(to_output_row(request, decision))
            traces.append(_trace_entry(request, decision, ledger, None))

            status_counts[decision.affordability_status.value] = (
                status_counts.get(decision.affordability_status.value, 0) + 1
            )
            method_counts[decision.recommended_payment_method.value] = (
                method_counts.get(decision.recommended_payment_method.value, 0) + 1
            )
            rule_counts[decision.status_rule] = rule_counts.get(decision.status_rule, 0) + 1
            for note in decision.notes:
                loud.append(f"{request.request_id}: {note}")
            if ledger.missing_amounts:
                missing_rows.append(f"{request.request_id}: {list(ledger.missing_amounts)}")
            if ledger.fx_fallbacks:
                fx_rows.append(f"{request.request_id}: {len(ledger.fx_fallbacks)} fallback(s)")

        except Exception as exc:  # noqa: BLE001 - one bad row must not lose 249
            detail = f"{type(exc).__name__}: {exc}"
            fallbacks.append(f"{request.request_id}: {detail}")
            rows.append(
                safe_default_row(
                    request.request_id,
                    request_date=request.request_date,
                    requested_amount=request.requested_amount,
                )
            )
            traces.append(
                _trace_entry(request, None, None, f"{detail}\n{traceback.format_exc()}")
            )

    target = Path(output_path or cfg.paths.output_csv)
    _guard_image_resolution(cfg, target, len(dataset.image_amounts))
    write_output_csv(str(target), rows)

    trace_target = Path(trace_path) if trace_path else target.with_suffix(".trace.jsonl")
    with trace_target.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in traces:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    observation = dataset.observation
    ocr_results = dataset.ocr_results
    model_notes: list[str] = []
    if use_model and not cfg.model.is_enabled():
        model_notes.append(
            f"{cfg.model.api_key_env_var} is not set -- the model layer was skipped "
            f"and every row took the deterministic path."
        )
    for outcome in ocr_results.values():
        if not outcome.usable:
            model_notes.append(
                f"{outcome.event_id}: {outcome.status.value} -- {outcome.rejection_reason}"
            )
    if observation is not None:
        model_notes.extend(f"discarded amendment -- {d}" for d in observation.discarded)
        model_notes.extend(f"observe error -- {e}" for e in observation.errors)

    summary = RunSummary(
        rows=len(rows),
        fallbacks=tuple(fallbacks),
        status_counts=status_counts,
        method_counts=method_counts,
        rule_counts=rule_counts,
        loud_notes=tuple(loud),
        missing_amount_rows=tuple(missing_rows),
        fx_fallback_rows=tuple(fx_rows),
        model_enabled=use_model and cfg.model.is_enabled(),
        ocr_extracted=sum(1 for o in ocr_results.values() if o.usable),
        ocr_failed=sum(1 for o in ocr_results.values() if not o.usable),
        amendments_applied=len(dataset.amendments),
        amendments_discarded=len(observation.discarded) if observation else 0,
        prefilter_kept=observation.prefilter.kept if observation else 0,
        prefilter_skipped=observation.prefilter.skipped if observation else 0,
        observe_batches=observation.batches if observation else 0,
        model_calls=recorder.calls,
        input_tokens=recorder.input_tokens,
        output_tokens=recorder.output_tokens,
        model_notes=tuple(model_notes),
    )
    return tuple(rows), summary
