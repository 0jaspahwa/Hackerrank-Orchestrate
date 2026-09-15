"""Enumerate candidate plans, filter for safety and eligibility, then rank.

The order is deliberate: eligibility and safety are FILTERS, not tie-breakers.
A plan the user rejects or cannot survive is never ranked at all.

CAPACITY IS COMPUTED ON THE BASELINE LEDGER. `amount_safe_to_pay` and
`earliest_date_for_full_payment` are what the user's cash flow bears BEFORE any
spending change, and they are reported that way. A spending change only decides
which PLAN is safe. Requests 06, 11 and 21 prove it: in each, `earliest` falls
AFTER `desired_completion_date`, yet a change makes a full payment safe today,
so the row is `affordable_with_plan` + `full_payment` while `earliest` still
reports the unchanged date.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from src.capacity import balance_path
from src.changes import (
    ChangeCandidate,
    apply_changes,
    find_minimal_change_set,
    to_change_set,
)
from src.config import DecisionConfig, PlanConfig
from src.contract import (
    AffordabilityStatus,
    PaymentMethod,
    PaymentPlan,
    SpendingChangeSet,
)
from src.ledger import CashFlow, InclusionReason, Ledger, Profile
from src.options import PaymentOption, accepts_method


class RequestRow(BaseModel):
    """One row of `requests.csv`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date | None
    allows_partial_payment: bool
    request_text: str = ""


class Candidate(BaseModel):
    """One way of paying, already known to be safe and eligible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: PaymentMethod
    plan: PaymentPlan
    total_paid: Decimal
    changes: tuple[ChangeCandidate, ...] = ()
    payment_option_id: str | None = None
    #: True when the whole request is settled by `desired_completion_date`.
    completes_by_deadline: bool = False

    @property
    def rank_key(self) -> tuple:
        """The spec's six keys, first difference wins.

        Key 3 (total paid) outranks key 4 (starts earlier): a fee-free plan beats
        a fee-bearing one that begins sooner.
        """
        start = self.plan.start_date
        return (
            not self.completes_by_deadline,
            len(self.changes) > 0,
            self.total_paid,
            start or date.max,
            len(self.plan),
            self.payment_option_id or "",
        )


class Decision(BaseModel):
    """Everything the output row and its explanation need."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: PaymentPlan
    earliest_date_for_full_payment: date | None
    spending_changes: SpendingChangeSet

    # -- trace, for explain.py and the JSONL audit --------------------------
    requested_amount: Decimal
    home_currency: str
    minimum_balance: Decimal
    trough_balance: Decimal
    trough_date: date | None
    desired_completion_date: date | None
    status_rule: int
    change_descriptions: tuple[str, ...] = ()
    eligible_option_ids: tuple[str, ...] = ()
    considered_methods: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def _payment_flows(plan: PaymentPlan) -> tuple[CashFlow, ...]:
    return tuple(
        CashFlow(
            on_date=entry.due_date,
            amount=-entry.amount,
            source_event_id="requested_payment",
            reason=InclusionReason.SCHEDULED_FUTURE,
            projected=False,
            description="the requested payment",
        )
        for entry in plan.entries
    )


def is_plan_safe(
    ledger: Ledger,
    plan: PaymentPlan,
    minimum_balance: Decimal,
    plan_config: PlanConfig,
) -> bool:
    """Does the balance stay at or above the minimum for the whole plan.

    The horizon stretches to the last payment when a plan outlives the forecast
    window. Beyond `window_end` no further income is assumed, which is the
    conservative reading and the only honest one -- nothing supports income out
    there.
    """
    if not plan.entries:
        return True

    horizon = max(ledger.window_end, plan.entries[-1].due_date)
    combined = ledger.model_copy(
        update={
            "flows": tuple(ledger.flows) + _payment_flows(plan),
            "window_end": horizon,
        }
    )
    trough = min(point.balance for point in balance_path(combined))
    return trough >= minimum_balance - plan_config.safety_tolerance


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def _completes_by(plan: PaymentPlan, deadline: date | None) -> bool:
    if not plan.entries:
        return False
    if deadline is None:
        return True
    return plan.entries[-1].due_date <= deadline


def _build_candidates(
    request: RequestRow,
    profile: Profile,
    ledger: Ledger,
    options: Sequence[PaymentOption],
    change_candidates: Sequence[ChangeCandidate],
    amount_safe: Decimal,
    earliest: date | None,
    decision: DecisionConfig,
    plan_config: PlanConfig,
) -> list[Candidate]:
    """Every safe, eligible way of completing the FULL request.

    Each shape is tried first with no spending change; only if that fails is the
    minimal change set searched for, because "requires no spending changes" is
    ranking key 2.
    """
    found: list[Candidate] = []

    def admit(
        method: PaymentMethod,
        plan: PaymentPlan,
        total: Decimal,
        option_id: str | None = None,
    ) -> None:
        """Add this shape, using a spending change only if it needs one."""
        if is_plan_safe(ledger, plan, profile.minimum_balance_to_keep, plan_config):
            found.append(
                Candidate(
                    method=method,
                    plan=plan,
                    total_paid=total,
                    payment_option_id=option_id,
                    completes_by_deadline=_completes_by(plan, request.desired_completion_date),
                )
            )
            return

        if not change_candidates:
            return
        rescue = find_minimal_change_set(
            ledger,
            change_candidates,
            decision.max_spending_changes,
            lambda adjusted: is_plan_safe(
                adjusted, plan, profile.minimum_balance_to_keep, plan_config
            ),
        )
        if rescue:
            found.append(
                Candidate(
                    method=method,
                    plan=plan,
                    total_paid=total,
                    changes=tuple(rescue),
                    payment_option_id=option_id,
                    completes_by_deadline=_completes_by(plan, request.desired_completion_date),
                )
            )

    # -- full payment on the request date ----------------------------------
    if accepts_method(profile, PaymentMethod.FULL_PAYMENT):
        admit(
            PaymentMethod.FULL_PAYMENT,
            PaymentPlan.of([(request.request_date, request.requested_amount)]),
            request.requested_amount,
        )

    # -- partial payment: ASP today, the remainder on the earliest safe date
    if (
        request.allows_partial_payment
        and accepts_method(profile, PaymentMethod.PARTIAL_PAYMENT)
        and earliest is not None
        and Decimal(0) < amount_safe < request.requested_amount
        and (
            request.desired_completion_date is None
            or earliest <= request.desired_completion_date
        )
    ):
        admit(
            PaymentMethod.PARTIAL_PAYMENT,
            PaymentPlan.of(
                [
                    (request.request_date, amount_safe),
                    (earliest, request.requested_amount - amount_safe),
                ]
            ),
            request.requested_amount,
        )

    # -- installments, verbatim from each eligible option -------------------
    for option in options:
        if not option.is_installments:
            continue
        admit(
            PaymentMethod.INSTALLMENTS,
            option.plan(),
            option.total_payable_amount,
            option.payment_option_id,
        )

    return found


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def decide(
    request: RequestRow,
    profile: Profile,
    ledger: Ledger,
    options: Sequence[PaymentOption],
    change_candidates: Sequence[ChangeCandidate],
    amount_safe: Decimal,
    earliest: date | None,
    *,
    decision: DecisionConfig,
    plan_config: PlanConfig,
) -> Decision:
    """Pick a status and a plan. Deterministic; no model call anywhere near it.

    `amount_safe` and `earliest` are passed in already computed on the BASELINE
    ledger and are never recomputed here -- this function must not be able to
    bend a capacity measure to suit a plan.
    """
    points = balance_path(ledger)
    trough = min(points, key=lambda p: p.balance)
    notes: list[str] = []

    accepts_full = accepts_method(profile, PaymentMethod.FULL_PAYMENT)

    def build(
        status: AffordabilityStatus,
        method: PaymentMethod,
        plan: PaymentPlan,
        changes: SpendingChangeSet,
        rule: int,
        descriptions: tuple[str, ...] = (),
    ) -> Decision:
        return Decision(
            request_id=request.request_id,
            amount_safe_to_pay=amount_safe,
            affordability_status=status,
            recommended_payment_method=method,
            payment_plan=plan,
            earliest_date_for_full_payment=earliest,
            spending_changes=changes,
            requested_amount=request.requested_amount,
            home_currency=ledger.home_currency,
            minimum_balance=profile.minimum_balance_to_keep,
            trough_balance=trough.balance,
            trough_date=trough.on_date,
            desired_completion_date=request.desired_completion_date,
            status_rule=rule,
            change_descriptions=descriptions,
            eligible_option_ids=tuple(o.payment_option_id for o in options),
            considered_methods=profile.payment_methods_user_will_consider,
            notes=tuple(notes),
        )

    # -- 1. full capacity today, and the user will pay in full -------------
    if amount_safe >= request.requested_amount and accepts_full:
        return build(
            AffordabilityStatus.AFFORDABLE_NOW,
            PaymentMethod.FULL_PAYMENT,
            PaymentPlan.of([(request.request_date, request.requested_amount)]),
            SpendingChangeSet.empty(),
            rule=1,
        )

    # -- 2. some safe, eligible plan completes the request by the deadline --
    candidates = _build_candidates(
        request,
        profile,
        ledger,
        options,
        change_candidates,
        amount_safe,
        earliest,
        decision,
        plan_config,
    )
    completing = [c for c in candidates if c.completes_by_deadline]
    if completing:
        best = min(completing, key=lambda c: c.rank_key)
        return build(
            AffordabilityStatus.AFFORDABLE_WITH_PLAN,
            best.method,
            best.plan,
            to_change_set(best.changes),
            rule=2,
            descriptions=tuple(c.description for c in best.changes),
        )

    # -- 3. a full payment becomes safe later, and the user will wait -------
    if earliest is not None and accepts_full:
        return build(
            AffordabilityStatus.AFFORDABLE_LATER,
            PaymentMethod.WAIT,
            PaymentPlan.of([(earliest, request.requested_amount)]),
            SpendingChangeSet.empty(),
            rule=3,
        )

    # -- 4. affordable later, but no eligible method can get there ----------
    if earliest is not None:
        # UNVALIDATED: never occurs in the 25 labelled rows. Implemented as
        # specified and logged loudly so these rows can be reviewed by hand.
        notes.append(
            f"STATUS RULE 4 FIRED (unvalidated): capacity exists by {earliest} but "
            f"the user accepts only {'|'.join(profile.payment_methods_user_will_consider)} "
            f"and no eligible plan reaches it."
        )
        return build(
            AffordabilityStatus.AFFORDABLE_LATER,
            PaymentMethod.NOT_RECOMMENDED,
            PaymentPlan.empty(),
            SpendingChangeSet.empty(),
            rule=4,
        )

    # -- 5. nothing works ---------------------------------------------------
    return build(
        AffordabilityStatus.NOT_AFFORDABLE,
        PaymentMethod.NOT_RECOMMENDED,
        PaymentPlan.empty(),
        SpendingChangeSet.empty(),
        rule=5,
    )
