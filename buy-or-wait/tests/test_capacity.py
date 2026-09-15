"""Capacity math, including a structural guard on the two signatures."""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.capacity import (
    amount_safe_to_pay,
    balance_path,
    earliest_date_for_full_payment,
    minimum_balance_reached,
)
from src.ledger import CashFlow, InclusionReason, Ledger

REQUEST_DATE = date(2025, 6, 10)
WINDOW_END = REQUEST_DATE + timedelta(days=90)


def flow(day_offset: int, amount: str, event_id: str = "e") -> CashFlow:
    return CashFlow(
        on_date=REQUEST_DATE + timedelta(days=day_offset),
        amount=Decimal(amount),
        source_event_id=event_id,
        reason=InclusionReason.PROJECTED_RECURRING,
        projected=True,
    )


def ledger(opening: str, *flows: CashFlow) -> Ledger:
    return Ledger(
        user_id="user_x",
        request_date=REQUEST_DATE,
        window_end=WINDOW_END,
        home_currency="EUR",
        opening_balance=Decimal(opening),
        flows=tuple(flows),
    )


# ---------------------------------------------------------------------------
# THE structural requirement
# ---------------------------------------------------------------------------

#: Anything that would couple a capacity measure to how the user wants to pay.
FORBIDDEN_PARAMETERS = (
    "payment_method",
    "payment_methods",
    "payment_methods_user_will_consider",
    "payment_option",
    "payment_options",
    "option",
    "options",
    "spending_change",
    "spending_changes",
    "changes",
    "profile",
    "plan",
    "preference",
    "preferences",
    "allows_partial_payment",
    "max_installment_months",
)


@pytest.mark.parametrize("function", [amount_safe_to_pay, earliest_date_for_full_payment])
def test_capacity_signatures_cannot_express_a_payment_preference(function) -> None:
    """Capacity is what the cash flow can bear, before anyone picks a method.

    This asserts the architecture, not a behaviour: if someone later threads a
    payment option or a spending change into these functions to fix a row, this
    test fails and says why.
    """
    parameters = set(inspect.signature(function).parameters)
    leaked = parameters & set(FORBIDDEN_PARAMETERS)
    assert not leaked, (
        f"{function.__name__} accepts {sorted(leaked)}. amount_safe_to_pay and "
        f"earliest_date_for_full_payment are CAPACITY measures and must not see "
        f"payment preferences, payment options, or spending changes."
    )


def test_capacity_functions_take_only_the_documented_parameters() -> None:
    assert list(inspect.signature(amount_safe_to_pay).parameters) == [
        "ledger",
        "minimum_balance",
        "requested_amount",
    ]
    assert list(inspect.signature(earliest_date_for_full_payment).parameters) == [
        "ledger",
        "minimum_balance",
        "requested_amount",
        "window_end",
    ]


# ---------------------------------------------------------------------------
# balance_path
# ---------------------------------------------------------------------------


def test_path_starts_at_the_opening_balance_on_the_request_date() -> None:
    points = balance_path(ledger("1000"))
    assert len(points) == 1
    assert points[0].on_date == REQUEST_DATE
    assert points[0].balance == Decimal("1000")


def test_path_accumulates_and_merges_same_day_flows() -> None:
    points = balance_path(ledger("1000", flow(5, "-100"), flow(5, "-50"), flow(10, "300")))
    assert [(p.on_date, p.balance) for p in points] == [
        (REQUEST_DATE, Decimal("1000")),
        (REQUEST_DATE + timedelta(days=5), Decimal("850")),
        (REQUEST_DATE + timedelta(days=10), Decimal("1150")),
    ]


def test_flows_outside_the_window_are_ignored() -> None:
    points = balance_path(ledger("1000", flow(200, "-999"), flow(-5, "-999")))
    assert len(points) == 1
    assert points[0].balance == Decimal("1000")


# ---------------------------------------------------------------------------
# amount_safe_to_pay
# ---------------------------------------------------------------------------


def test_amount_safe_is_the_trough_minus_the_minimum() -> None:
    book = ledger("1000", flow(5, "-400"), flow(20, "800"))
    assert minimum_balance_reached(book) == Decimal("600")
    assert amount_safe_to_pay(book, Decimal("100"), Decimal("10000")) == Decimal("500")


def test_amount_safe_is_capped_at_the_requested_amount() -> None:
    book = ledger("1000")
    assert amount_safe_to_pay(book, Decimal("100"), Decimal("250")) == Decimal("250")


def test_amount_safe_is_zero_when_the_trough_is_below_the_minimum() -> None:
    book = ledger("1000", flow(5, "-950"))
    assert amount_safe_to_pay(book, Decimal("100"), Decimal("500")) == Decimal("0")


def test_amount_safe_never_goes_negative() -> None:
    book = ledger("50", flow(5, "-40"))
    assert amount_safe_to_pay(book, Decimal("1000"), Decimal("500")) == Decimal("0")


def test_amount_safe_rounds_down_never_up() -> None:
    """Rounding a maximum-safe quantity up can breach the minimum by a cent."""
    book = ledger("1000.999")
    assert amount_safe_to_pay(book, Decimal("0"), Decimal("100000")) == Decimal("1000.99")


def test_a_later_trough_still_governs() -> None:
    """The dip can be months out; capacity is not about the first few days."""
    book = ledger("1000", flow(5, "200"), flow(80, "-900"))
    assert amount_safe_to_pay(book, Decimal("0"), Decimal("100000")) == Decimal("300")


# ---------------------------------------------------------------------------
# earliest_date_for_full_payment
# ---------------------------------------------------------------------------


def test_earliest_is_the_request_date_when_affordable_today() -> None:
    book = ledger("1000", flow(30, "500"))
    assert earliest_date_for_full_payment(
        book, Decimal("100"), Decimal("900"), WINDOW_END
    ) == REQUEST_DATE


def test_earliest_waits_for_the_credit_that_lifts_the_suffix_minimum() -> None:
    book = ledger("1000", flow(5, "-500"), flow(35, "2000"))
    got = earliest_date_for_full_payment(book, Decimal("100"), Decimal("1200"), WINDOW_END)
    assert got == REQUEST_DATE + timedelta(days=35)


def test_earliest_is_none_when_never_affordable_in_the_window() -> None:
    book = ledger("1000", flow(5, "-500"))
    assert earliest_date_for_full_payment(book, Decimal("100"), Decimal("5000"), WINDOW_END) is None


def test_a_dip_after_a_credit_pushes_the_earliest_date_out() -> None:
    """A payment is only safe if the balance survives everything AFTER it."""
    book = ledger("1000", flow(10, "5000"), flow(20, "-5000"), flow(40, "6000"))
    got = earliest_date_for_full_payment(book, Decimal("0"), Decimal("2000"), WINDOW_END)
    assert got == REQUEST_DATE + timedelta(days=40)


def test_window_end_truncates_the_search() -> None:
    book = ledger("1000", flow(5, "-900"), flow(60, "9000"))
    early_cutoff = REQUEST_DATE + timedelta(days=30)
    assert earliest_date_for_full_payment(book, Decimal("0"), Decimal("2000"), early_cutoff) is None
    assert (
        earliest_date_for_full_payment(book, Decimal("0"), Decimal("2000"), WINDOW_END)
        == REQUEST_DATE + timedelta(days=60)
    )


def test_suffix_minimum_is_non_decreasing_so_the_answer_is_the_first_hit() -> None:
    book = ledger("1000", flow(10, "-200"), flow(20, "700"), flow(40, "-100"), flow(60, "900"))
    points = balance_path(book)

    running = None
    suffix = []
    for point in reversed(points):
        running = point.balance if running is None else min(running, point.balance)
        suffix.append(running)
    suffix.reverse()
    assert suffix == sorted(suffix), "suffix_min must be non-decreasing in p"

    requested = Decimal("500")
    expected = next(
        (p.on_date for p, s in zip(points, suffix) if s - Decimal("0") >= requested), None
    )
    assert earliest_date_for_full_payment(book, Decimal("0"), requested, WINDOW_END) == expected


def test_capacity_is_independent_of_how_the_user_wants_to_pay() -> None:
    """Same ledger, same numbers. Nothing about preferences can reach here."""
    book = ledger("1000", flow(5, "-400"), flow(20, "800"))
    first = amount_safe_to_pay(book, Decimal("100"), Decimal("10000"))
    second = amount_safe_to_pay(book, Decimal("100"), Decimal("10000"))
    assert first == second == Decimal("500")
