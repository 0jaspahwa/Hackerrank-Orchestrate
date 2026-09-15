"""Spending changes: which commitments a user has told us they will bend.

A change is legal only when the event is non-fixed AND its category appears in
the matching preference list -- `expense_categories_user_is_willing_to_reduce`
for a reduction, `..._to_stop` for a stop. `minimum_allowed_amount` is the floor
for a reduction, and in both labelled reductions the chosen amount IS that floor.

The search is exhaustive. Each user has fewer than ten changeable events and at
most three may be used, so every subset is enumerated and RE-SIMULATED against
the ledger. No greedy heuristic: greedy picks the biggest saving first, which is
wrong here because the objective is the SMALLEST disruption that clears the gap.

A change applies to every projected occurrence of its series inside the window.
The trough can be several billing cycles out, so one change may free its amount
more than once -- verified against request_11, which needs two occurrences of a
single reduction.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import combinations
from typing import Callable, Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from src.config import DecisionConfig
from src.contract import ChangeAction, SpendingChange, SpendingChangeSet
from src.ledger import CashFlow, FinancialEvent, Ledger, Profile


class ChangeCandidate(BaseModel):
    """One legal change to one recurring commitment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    action: ChangeAction
    description: str
    category: str
    #: The event's own amount, in its own currency, as the output must quote it.
    original_amount: Decimal
    #: `None` for a stop; the floor for a reduction.
    new_amount: Decimal | None

    @property
    def retained_fraction(self) -> Decimal:
        """Share of each occurrence that survives the change."""
        if self.action is ChangeAction.STOP or self.new_amount is None:
            return Decimal(0)
        if self.original_amount == 0:
            return Decimal(0)
        return self.new_amount / self.original_amount

    def to_spending_change(self) -> SpendingChange:
        if self.action is ChangeAction.STOP:
            return SpendingChange.stop(self.event_id)
        assert self.new_amount is not None
        return SpendingChange.reduce_to(self.event_id, self.new_amount)


def _latest_of_series(events: Sequence[FinancialEvent]) -> dict[str, FinancialEvent]:
    """The most recent event per description -- the id the output must cite."""
    latest: dict[str, FinancialEvent] = {}
    for event in events:
        if event.amount is None:
            continue
        seen = latest.get(event.description)
        if seen is None or event.cash_date > seen.cash_date:
            latest[event.description] = event
    return latest


def candidate_changes(
    profile: Profile,
    events: Sequence[FinancialEvent],
    decision: DecisionConfig,
) -> tuple[ChangeCandidate, ...]:
    """Every legal single change, keyed to the most recent event of its series.

    Protected categories are excluded even when the flexibility column would
    allow the change: the user named them as off limits.
    """
    protected = set(profile.expense_categories_to_protect)
    may_reduce = set(profile.expense_categories_user_is_willing_to_reduce)
    may_stop = set(profile.expense_categories_user_is_willing_to_stop)

    candidates: list[ChangeCandidate] = []
    for event in _latest_of_series(events).values():
        if event.flexibility in decision.immutable_flexibility:
            continue
        if event.category in protected:
            continue
        assert event.amount is not None

        if event.flexibility in decision.stoppable_flexibility and event.category in may_stop:
            candidates.append(
                ChangeCandidate(
                    event_id=event.event_id,
                    action=ChangeAction.STOP,
                    description=event.description,
                    category=event.category,
                    original_amount=event.amount,
                    new_amount=None,
                )
            )

        reducible = event.flexibility in decision.reducible_flexibility
        floor = event.minimum_allowed_amount
        if reducible and event.category in may_reduce and floor is not None and floor < event.amount:
            candidates.append(
                ChangeCandidate(
                    event_id=event.event_id,
                    action=ChangeAction.REDUCE_TO,
                    description=event.description,
                    category=event.category,
                    original_amount=event.amount,
                    new_amount=floor,
                )
            )

    return tuple(sorted(candidates, key=lambda c: (c.event_id, c.action.value)))


def _affects(flow: CashFlow, candidate: ChangeCandidate) -> bool:
    """Does this change touch this flow.

    Matched on description as well as event id: the projection cites the series'
    most recent event, but the flows it emits may carry that id or simply share
    the description.
    """
    return flow.source_event_id == candidate.event_id or (
        bool(candidate.description) and flow.description == candidate.description
    )


def apply_changes(ledger: Ledger, changes: Sequence[ChangeCandidate]) -> Ledger:
    """A copy of the ledger with every affected occurrence adjusted.

    Scaling by `retained_fraction` keeps the arithmetic in the home currency and
    sidesteps converting a `minimum_allowed_amount` quoted in the event's own
    currency.
    """
    if not changes:
        return ledger

    adjusted: list[CashFlow] = []
    for flow in ledger.flows:
        hit = next((c for c in changes if _affects(flow, c)), None)
        if hit is None or flow.amount >= 0:
            # Changes only bend spending. A credit is never "reduced".
            adjusted.append(flow)
            continue
        fraction = hit.retained_fraction
        if fraction == 0:
            continue
        adjusted.append(flow.model_copy(update={"amount": flow.amount * fraction}))

    return ledger.model_copy(update={"flows": tuple(adjusted)})


def total_freed(ledger: Ledger, changes: Sequence[ChangeCandidate]) -> Decimal:
    """Cash the change set releases inside the window, across all occurrences."""
    before = sum((f.amount for f in ledger.flows if f.amount < 0), Decimal(0))
    after = sum((f.amount for f in apply_changes(ledger, changes).flows if f.amount < 0), Decimal(0))
    return after - before


def _conflicts(changes: Sequence[ChangeCandidate]) -> bool:
    """Stop and reduce_to on the same event are mutually exclusive."""
    ids = [c.event_id for c in changes]
    return len(ids) != len(set(ids))


def enumerate_change_sets(
    candidates: Sequence[ChangeCandidate],
    max_changes: int,
) -> Iterable[tuple[ChangeCandidate, ...]]:
    """Every legal subset, smallest first, including the empty set."""
    for size in range(max_changes + 1):
        for subset in combinations(candidates, size):
            if _conflicts(subset):
                continue
            yield subset


def find_minimal_change_set(
    ledger: Ledger,
    candidates: Sequence[ChangeCandidate],
    max_changes: int,
    is_sufficient: Callable[[Ledger], bool],
) -> tuple[ChangeCandidate, ...] | None:
    """The least disruptive change set that satisfies `is_sufficient`.

    `is_sufficient` is supplied by the caller and receives only a ledger, so this
    module never learns what a payment plan is.

    Ordering, after the spec's own rules and documented as an addition:
      1. smallest total cash freed  -- take the least the user has to give up
      2. fewest changes
      3. lowest event ids
    Smallest-freed leads deliberately. request_21's label uses TWO changes
    freeing 34.50 in preference to ONE change freeing 47.00, so fewest-changes
    cannot be the first key.
    """
    viable: list[tuple[ChangeCandidate, ...]] = []
    for subset in enumerate_change_sets(candidates, max_changes):
        if is_sufficient(apply_changes(ledger, subset)):
            viable.append(subset)

    if not viable:
        return None

    return min(
        viable,
        key=lambda subset: (
            total_freed(ledger, subset),
            len(subset),
            tuple(sorted(c.event_id for c in subset)),
        ),
    )


def to_change_set(changes: Sequence[ChangeCandidate]) -> SpendingChangeSet:
    """Render candidates into the contract's serialisable form."""
    return SpendingChangeSet(changes=tuple(c.to_spending_change() for c in changes))
