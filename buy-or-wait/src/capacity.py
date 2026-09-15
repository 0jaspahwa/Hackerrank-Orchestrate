"""PURE capacity math over the projected balance path.

    window = [request_date, request_date + 90]
    B(d)   = balance path over the window, EXCLUDING the request itself
    amount_safe_to_pay = clamp(min B(d) - minimum_balance, 0, requested_amount)
    earliest = first p where suffix_min(p) - minimum_balance >= requested_amount

`suffix_min` is non-decreasing in p, so the second is a single backward pass,
not a search.

STRUCTURAL REQUIREMENT -- the most important one in the project. Both public
functions are CAPACITY measures: what the user's cash flow can bear, before
anyone asks how they would like to pay. Their signatures accept no payment
method, no payment option, no spending change, and nothing derived from one, so
the forbidden coupling cannot be expressed. If you find yourself wanting to pass
one, the design upstream is wrong, not the signature.

`earliest_date_for_full_payment` may legitimately equal `request_date` even when
the final recommendation is installments -- that is the plan layer's business.

No I/O, no clock, no randomness, no globals. Runs with no API key present.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from src.contract import quantize_down
from src.ledger import Ledger


class BalancePoint(BaseModel):
    """The balance on one date, AFTER every flow landing that date."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_date: date
    balance: Decimal
    #: Net movement on this date. Zero at the opening point.
    delta: Decimal


def balance_path(ledger: Ledger) -> tuple[BalancePoint, ...]:
    """The step function B(d) across the window, excluding the request itself.

    One point per distinct date on which money moves, plus `request_date` as the
    opening point. Because the balance is constant between flow dates, these
    points are the only places a minimum can occur.
    """
    by_date: dict[date, Decimal] = {}
    for flow in ledger.flows:
        if flow.on_date < ledger.request_date or flow.on_date > ledger.window_end:
            continue
        by_date[flow.on_date] = by_date.get(flow.on_date, Decimal(0)) + flow.amount

    points: list[BalancePoint] = []
    running = ledger.opening_balance

    opening_delta = by_date.pop(ledger.request_date, Decimal(0))
    running += opening_delta
    points.append(
        BalancePoint(on_date=ledger.request_date, balance=running, delta=opening_delta)
    )

    for on_date in sorted(by_date):
        running += by_date[on_date]
        points.append(BalancePoint(on_date=on_date, balance=running, delta=by_date[on_date]))

    return tuple(points)


def minimum_balance_reached(ledger: Ledger) -> Decimal:
    """The trough of B(d) over the window. The one number capacity rests on."""
    return min(point.balance for point in balance_path(ledger))


def amount_safe_to_pay(
    ledger: Ledger,
    minimum_balance: Decimal,
    requested_amount: Decimal,
) -> Decimal:
    """How much of the request is safe to pay ON `request_date`.

    clamp(min B(d) - minimum_balance, 0, requested_amount), rounded DOWN to the
    minor unit: this is a maximum-safe quantity, and rounding up can breach the
    minimum balance by a cent.

    Takes no payment method, option, or spending change -- see the module
    docstring.
    """
    headroom = minimum_balance_reached(ledger) - minimum_balance
    if headroom <= 0:
        return Decimal(0)
    return quantize_down(min(headroom, requested_amount))


def earliest_date_for_full_payment(
    ledger: Ledger,
    minimum_balance: Decimal,
    requested_amount: Decimal,
    window_end: date,
) -> date | None:
    """The first date a single full payment is safe, or None if never in window.

    A payment on date p is safe when the balance stays at or above
    `minimum_balance` for the whole remainder of the window after it -- that is,
    `suffix_min(p) - minimum_balance >= requested_amount`. `suffix_min` only
    rises as p advances, so one backward pass finds the answer.

    Balances are read AFTER that date's flows, which is the conservative
    reading: the payment and that day's committed debits must both clear.

    Takes no payment method, option, or spending change -- see the module
    docstring.
    """
    points = [p for p in balance_path(ledger) if p.on_date <= window_end]
    if not points:
        return None

    required = minimum_balance + requested_amount
    answer: date | None = None
    suffix_min: Decimal | None = None

    for point in reversed(points):
        suffix_min = point.balance if suffix_min is None else min(suffix_min, point.balance)
        if suffix_min >= required:
            answer = point.on_date
        else:
            # suffix_min is non-decreasing in p: once it fails here it fails for
            # every earlier date, so nothing before this can qualify.
            break

    return answer
