"""`request_payment_options.csv`: parse, expand schedules, filter for eligibility.

Every request carries 2-4 options and exactly one of them is `full_payment`
(verified: 275 of 275). The rest are installment plans.

Two hard filters decide which options a user may be offered, and both run BEFORE
any ranking:

1. The method must appear in `payment_methods_user_will_consider`.
2. For installments, the plan's span must fit inside `max_installment_months`.
   A blank `max_installment_months` means the user will not consider
   installments at all, however short.

An installment schedule is reproduced VERBATIM from the option row -- the
amount is `payment_amount`, never a recomputed division of the request.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from src.contract import PaymentMethod, PaymentPlan, to_money
from src.ledger import Profile


class PaymentOption(BaseModel):
    """One row of `request_payment_options.csv`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    #: Blank for a single full payment.
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal

    @field_validator("payment_amount", "financing_fee", "total_payable_amount", mode="before")
    @classmethod
    def _money(cls, v: object) -> Decimal:
        return to_money(v)  # type: ignore[arg-type]

    @field_validator("payment_frequency_days", mode="before")
    @classmethod
    def _blank_frequency(cls, v: object) -> int | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return int(v)  # type: ignore[arg-type]

    @property
    def is_installments(self) -> bool:
        return self.payment_method is PaymentMethod.INSTALLMENTS

    def schedule(self) -> tuple[tuple[date, Decimal], ...]:
        """`first_payment_date + k * payment_frequency_days` for k in 0..n-1.

        Amount is `payment_amount` on every line, exactly as supplied.
        """
        step = self.payment_frequency_days or 0
        return tuple(
            (self.first_payment_date + timedelta(days=step * k), self.payment_amount)
            for k in range(self.number_of_payments)
        )

    def plan(self) -> PaymentPlan:
        return PaymentPlan.of(self.schedule())

    @property
    def last_payment_date(self) -> date:
        return self.schedule()[-1][0]

    def span_days(self) -> int:
        """Calendar days from the first payment to the last."""
        return (self.last_payment_date - self.first_payment_date).days

    def span_months(self, days_per_month: int) -> int:
        """Whole months the plan occupies, rounded up.

        Measured from the schedule rather than from `number_of_payments`, so a
        weekly plan is not mistaken for a plan of that many months.
        """
        if not self.is_installments:
            return 0
        return max(1, ceil((self.span_days() + (self.payment_frequency_days or 0)) / days_per_month))


def load_payment_options(path: str | Path) -> dict[str, tuple[PaymentOption, ...]]:
    """Read the options file, grouped by `request_id`, in file order."""
    grouped: dict[str, list[PaymentOption]] = defaultdict(list)
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            option = PaymentOption(**row)
            grouped[option.request_id].append(option)
    return {request_id: tuple(rows) for request_id, rows in grouped.items()}


def accepts_method(profile: Profile, method: PaymentMethod) -> bool:
    """Is this method in the user's pipe-separated preference list."""
    return method.value in profile.payment_methods_user_will_consider


def eligible_options(
    options: tuple[PaymentOption, ...],
    profile: Profile,
    *,
    days_per_month: int,
) -> tuple[PaymentOption, ...]:
    """The options a user would actually consider, before any ranking.

    `max_installment_months` is the decisive filter: in every labelled
    installments row it is what removed the long 15-to-21-payment alternative
    and left the short plan that the label chose.
    """
    keep: list[PaymentOption] = []
    for option in options:
        if not accepts_method(profile, option.payment_method):
            continue
        if option.is_installments:
            if profile.max_installment_months is None:
                # Blank means the user will not consider installments at all.
                continue
            if option.span_months(days_per_month) > profile.max_installment_months:
                continue
        keep.append(option)
    return tuple(keep)


def full_payment_option(options: tuple[PaymentOption, ...]) -> PaymentOption | None:
    """The single `full_payment` row, if the request has one."""
    for option in options:
        if option.payment_method is PaymentMethod.FULL_PAYMENT:
            return option
    return None
