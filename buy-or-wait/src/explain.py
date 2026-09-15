"""Compose `decision_explanation` from the decision trace, in code.

No model call. The scored property is CONSISTENCY with the numeric columns, so
every figure quoted here is read off the `Decision` rather than recomputed --
an explanation that disagrees with its own row is worse than a terse one.

Each template names the status, the binding constraint (the trough and when it
bites, or the deadline), the key figures, and any spending changes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

from src.contract import AffordabilityStatus, PaymentMethod
from src.plans import Decision

#: Rendered as "15 September 2024", matching the labelled explanations.
LONG_DATE = "%-d %B %Y"
LONG_DATE_WINDOWS = "%#d %B %Y"


def format_long_date(value: date) -> str:
    """Day-month-year with no leading zero, portably."""
    return f"{value.day} {value:%B} {value.year}"


def format_amount(currency: str, amount: Decimal | int | float) -> str:
    """`EUR 1,234.56`, with the decimals dropped when the value is whole."""
    quantised = Decimal(str(amount)).quantize(Decimal("0.01"))
    if quantised == quantised.to_integral_value():
        return f"{currency} {int(quantised):,}"
    return f"{currency} {quantised:,.2f}"


def _change_phrase(descriptions: Sequence[str], decision: Decision) -> str:
    """"Stop the family streaming plan" / "Adjust two commitments"."""
    actions = decision.spending_changes.changes
    if not actions:
        return ""
    if len(actions) == 1 and descriptions:
        verb = "Stop" if actions[0].action.value == "stop" else "Reduce"
        return f"{verb} the {descriptions[0].lower()}"
    named = ", ".join(d.lower() for d in descriptions) if descriptions else "the flexible items"
    return f"Adjust {named}"


def _floor_phrase(decision: Decision) -> str:
    return format_amount(decision.home_currency, decision.minimum_balance)


def explain(decision: Decision) -> str:
    """One grounded sentence or two, consistent with every numeric column."""
    currency = decision.home_currency
    requested = format_amount(currency, decision.requested_amount)
    floor = _floor_phrase(decision)
    plan = decision.payment_plan
    change_text = _change_phrase(decision.change_descriptions, decision)

    # -- affordable now -----------------------------------------------------
    if decision.affordability_status is AffordabilityStatus.AFFORDABLE_NOW:
        return (
            f"Pay {requested} today. This leaves at least "
            f"{format_amount(currency, decision.trough_balance - decision.requested_amount)} "
            f"available over the next 90 days, above the {floor} minimum."
        )

    # -- affordable with a plan --------------------------------------------
    if decision.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN:
        if decision.recommended_payment_method is PaymentMethod.INSTALLMENTS:
            first = plan.entries[0]
            return (
                f"Use {len(plan)} installments of "
                f"{format_amount(currency, first.amount)}, starting "
                f"{format_long_date(first.due_date)}. This completes the full "
                f"{requested} and keeps the {floor} minimum protected."
            )
        if decision.recommended_payment_method is PaymentMethod.PARTIAL_PAYMENT:
            first, second = plan.entries[0], plan.entries[1]
            return (
                f"Pay {format_amount(currency, first.amount)} today and the remaining "
                f"{format_amount(currency, second.amount)} on "
                f"{format_long_date(second.due_date)}. This completes the full request "
                f"and keeps the {floor} minimum protected."
            )
        if change_text:
            return (
                f"{change_text}, then pay {requested} today. Without that change the "
                f"balance would fall to "
                f"{format_amount(currency, decision.trough_balance - decision.requested_amount)} "
                f"on {format_long_date(decision.trough_date)} and breach the {floor} minimum."
            )
        return (
            f"Pay {requested} today. This keeps the {floor} minimum protected "
            f"through the next 90 days."
        )

    # -- affordable later ---------------------------------------------------
    if decision.affordability_status is AffordabilityStatus.AFFORDABLE_LATER:
        if decision.recommended_payment_method is PaymentMethod.WAIT and plan.entries:
            when = format_long_date(plan.entries[0].due_date)
            return (
                f"Pay {requested} in full on {when}. Paying earlier would take the "
                f"balance below the {floor} minimum, which it reaches on "
                f"{format_long_date(decision.trough_date)}."
            )
        when = format_long_date(decision.earliest_date_for_full_payment) if decision.earliest_date_for_full_payment else "a later date"
        return (
            f"The full {requested} only becomes safe on {when}, and none of the "
            f"payment methods this user accepts reaches that date. Do not proceed now."
        )

    # -- not affordable -----------------------------------------------------
    deadline = (
        f" by {format_long_date(decision.desired_completion_date)}"
        if decision.desired_completion_date
        else ""
    )
    safe_now = format_amount(currency, decision.amount_safe_to_pay)
    return (
        f"Do not make this payment{deadline}. Only {safe_now} of the {requested} is "
        f"safe, and none of the available options keeps the {floor} minimum protected "
        f"within the next 90 days."
    )
