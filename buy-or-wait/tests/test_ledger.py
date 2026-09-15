"""Ledger construction: filtering, provenance, currency, and projection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.config import ForecastConfig, FxConfig, TransferConfig
from src.fx import RateQuote, build_rate_table
from src.ledger import (
    AmountMode,
    Direction,
    EventStatus,
    ExclusionReason,
    FinancialEvent,
    InclusionReason,
    IncomeMode,
    ProjectionRule,
    Profile,
    SelectorFamily,
    SelectorSpec,
    add_months,
    build_ledger,
    collapse_chains,
    detect_internal_transfers,
)

REQUEST_DATE = date(2025, 6, 10)
FORECAST = ForecastConfig()
FX = FxConfig()
TRANSFER = TransferConfig()

RATES = build_rate_table(
    [
        RateQuote(
            rate_date=date(2025, 6, 15),
            from_currency="USD",
            to_currency="IDR",
            rate=Decimal("15000"),
        )
    ]
)


def profile(**overrides) -> Profile:
    base = {
        "user_id": "user_x",
        "home_currency": "IDR",
        "current_available_balance": Decimal("1000000"),
        "minimum_balance_to_keep": Decimal("100000"),
    }
    base.update(overrides)
    return Profile(**base)


def event(event_id: str, **overrides) -> FinancialEvent:
    base = {
        "event_id": event_id,
        "user_id": "user_x",
        "event_type": "expense",
        "description": "Something",
        "category": "groceries",
        "direction": Direction.DEBIT,
        "amount": Decimal("1000"),
        "currency": "IDR",
        "event_date": date(2025, 6, 1),
        "settlement_date": date(2025, 6, 1),
        "status": EventStatus.SETTLED,
        "linked_event_id": None,
        "flexibility": "fixed",
        "minimum_allowed_amount": None,
    }
    base.update(overrides)
    return FinancialEvent(**base)


def ledger_of(events, *, rule=None, prof=None, image_amounts=None):
    return build_ledger(
        "user_x",
        REQUEST_DATE,
        prof or profile(),
        events,
        RATES,
        image_amounts or {},
        rule or ProjectionRule(selector="trailing_30"),
        forecast=FORECAST,
        fx=FX,
    )


def reasons(ledger) -> dict[str, ExclusionReason]:
    return {x.event_id: x.reason for x in ledger.excluded}


# ---------------------------------------------------------------------------
# Date arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchor", "months", "expected"),
    [
        (date(2025, 1, 31), 1, date(2025, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2025, 1, 31), 3, date(2025, 4, 30)),
        (date(2025, 12, 15), 1, date(2026, 1, 15)),
        (date(2025, 6, 10), 0, date(2025, 6, 10)),
    ],
)
def test_add_months_clamps_day_to_month_length(anchor, months, expected) -> None:
    assert add_months(anchor, months) == expected


# ---------------------------------------------------------------------------
# Rule 1: opening balance
# ---------------------------------------------------------------------------


def test_settled_history_is_never_replayed_against_the_opening_balance() -> None:
    """The profile balance is already net of settled history."""
    history = [
        event("e1", amount=Decimal("50000"), event_date=date(2025, 5, 1), settlement_date=date(2025, 5, 1)),
    ]
    ledger = ledger_of(history, rule=ProjectionRule(selector="day_stable_2"))
    assert ledger.opening_balance == Decimal("1000000")
    # Seen only once, so day_stable_2 will not project it either.
    assert ledger.flows == ()


def test_window_end_is_ninety_days_after_the_request() -> None:
    ledger = ledger_of([])
    assert (ledger.window_end - ledger.request_date).days == FORECAST.horizon_days


# ---------------------------------------------------------------------------
# Rule 2: exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [EventStatus.CANCELLED, EventStatus.FAILED, EventStatus.UNREALIZED])
def test_dead_statuses_are_excluded(status) -> None:
    ledger = ledger_of([event("e1", status=status, event_date=date(2025, 6, 20))])
    assert reasons(ledger)["e1"] is ExclusionReason.STATUS_EXCLUDED
    assert ledger.flows == ()


def test_non_cash_is_excluded() -> None:
    ledger = ledger_of([event("e1", direction=Direction.NON_CASH, status=EventStatus.SCHEDULED)])
    assert reasons(ledger)["e1"] is ExclusionReason.NON_CASH


def test_pending_credits_are_excluded_but_pending_debits_are_not() -> None:
    credit = event(
        "e_credit",
        direction=Direction.CREDIT,
        amount=Decimal("1000"),
        status=EventStatus.PENDING,
        event_date=date(2025, 6, 12),
        settlement_date=date(2025, 6, 12),
    )
    debit = event(
        "e_debit",
        amount=Decimal("2500"),
        status=EventStatus.PENDING,
        event_date=date(2025, 6, 12),
        settlement_date=date(2025, 6, 14),
    )
    ledger = ledger_of([credit, debit])

    assert reasons(ledger)["e_credit"] is ExclusionReason.PENDING_CREDIT
    landed = [f for f in ledger.flows if f.source_event_id == "e_debit"]
    assert len(landed) == 1
    assert landed[0].reason is InclusionReason.PENDING_DEBIT


# ---------------------------------------------------------------------------
# Rule 3 and 4: explicit future flows
# ---------------------------------------------------------------------------


def test_pending_debit_lands_on_settlement_date_not_event_date() -> None:
    ledger = ledger_of(
        [
            event(
                "e1",
                status=EventStatus.PENDING,
                event_date=date(2025, 6, 11),
                settlement_date=date(2025, 6, 25),
            )
        ]
    )
    assert [f.on_date for f in ledger.flows] == [date(2025, 6, 25)]


def test_pending_debit_dated_before_the_request_is_clamped_forward() -> None:
    ledger = ledger_of(
        [
            event(
                "e1",
                status=EventStatus.PENDING,
                event_date=date(2025, 6, 1),
                settlement_date=date(2025, 6, 2),
            )
        ],
        rule=ProjectionRule(selector="trailing_30", include_request_date=True),
    )
    assert [f.on_date for f in ledger.flows] == [REQUEST_DATE]


def test_scheduled_future_event_is_included_at_its_event_date() -> None:
    ledger = ledger_of(
        [
            event(
                "e1",
                status=EventStatus.SCHEDULED,
                direction=Direction.CREDIT,
                amount=Decimal("7000"),
                event_date=date(2025, 7, 15),
                settlement_date=date(2025, 7, 15),
            )
        ]
    )
    assert len(ledger.flows) == 1
    flow = ledger.flows[0]
    assert flow.on_date == date(2025, 7, 15)
    assert flow.amount == Decimal("7000")
    assert flow.reason is InclusionReason.SCHEDULED_FUTURE
    assert flow.projected is False


def test_events_beyond_the_window_are_excluded() -> None:
    ledger = ledger_of(
        [
            event(
                "e1",
                status=EventStatus.SCHEDULED,
                event_date=date(2026, 6, 1),
                settlement_date=date(2026, 6, 1),
            )
        ]
    )
    assert reasons(ledger)["e1"] is ExclusionReason.OUTSIDE_WINDOW


def test_debits_are_negative_and_credits_positive() -> None:
    ledger = ledger_of(
        [
            event(
                "d",
                amount=Decimal("1000"),
                status=EventStatus.SCHEDULED,
                event_date=date(2025, 7, 1),
                settlement_date=None,
            ),
            event(
                "c",
                amount=Decimal("2500"),
                status=EventStatus.SCHEDULED,
                direction=Direction.CREDIT,
                event_date=date(2025, 7, 2),
                settlement_date=None,
            ),
        ]
    )
    signs = {f.source_event_id: f.amount for f in ledger.flows}
    assert signs["d"] < 0 < signs["c"]


# ---------------------------------------------------------------------------
# Rule 5: internal transfers
# ---------------------------------------------------------------------------


def test_dataset_contains_no_internal_transfer_pairs() -> None:
    """DATA INVARIANT. The internal-transfer hypothesis was falsified.

    Across every user, the only equal-and-opposite pairs are `linked_event_id`
    refund lifecycles, which `collapse_chains` already owns. If this ever fails,
    a real transfer has appeared in the data and the exclusion -- removed from
    `build_ledger` -- must be reconsidered. See CLAUDE.md D1.
    """
    from collections import defaultdict

    from src.config import default_config
    from src.ledger import load_events

    cfg = default_config()
    by_user: dict[str, list] = defaultdict(list)
    for row in load_events(cfg.paths.financial_events_csv):
        by_user[row.user_id].append(row)

    offenders: list[str] = []
    for user, rows in by_user.items():
        chained, _ = collapse_chains(rows)
        paired, _ = detect_internal_transfers(
            rows, chained=chained, transfer=TRANSFER, forecast=FORECAST
        )
        if paired:
            offenders.append(f"{user}: {sorted(paired)}")

    assert not offenders, f"real internal transfers appeared in the data: {offenders}"


def test_transfer_detector_is_not_wired_into_the_pipeline() -> None:
    """The falsified rule must stay out of `build_ledger`'s signature."""
    import inspect

    assert "transfer" not in inspect.signature(build_ledger).parameters


def test_matching_debit_and_credit_pair_excludes_both_legs() -> None:
    pair = [
        event("out", amount=Decimal("500000"), event_date=date(2025, 6, 2), settlement_date=date(2025, 6, 2)),
        event(
            "in",
            direction=Direction.CREDIT,
            amount=Decimal("500000"),
            event_date=date(2025, 6, 3),
            settlement_date=date(2025, 6, 3),
        ),
    ]
    paired, excluded = detect_internal_transfers(pair, chained=frozenset(), transfer=TRANSFER, forecast=FORECAST)
    assert paired == frozenset({"out", "in"})
    assert {x.reason for x in excluded} == {ExclusionReason.INTERNAL_TRANSFER}


def test_pair_outside_the_window_is_not_a_transfer() -> None:
    pair = [
        event("out", amount=Decimal("500000"), event_date=date(2025, 6, 1), settlement_date=date(2025, 6, 1)),
        event(
            "in",
            direction=Direction.CREDIT,
            amount=Decimal("500000"),
            event_date=date(2025, 6, 20),
            settlement_date=date(2025, 6, 20),
        ),
    ]
    paired, _ = detect_internal_transfers(pair, chained=frozenset(), transfer=TRANSFER, forecast=FORECAST)
    assert paired == frozenset()


def test_unequal_amounts_are_not_a_transfer() -> None:
    pair = [
        event("out", amount=Decimal("500000")),
        event("in", direction=Direction.CREDIT, amount=Decimal("400000")),
    ]
    paired, _ = detect_internal_transfers(pair, chained=frozenset(), transfer=TRANSFER, forecast=FORECAST)
    assert paired == frozenset()


def test_a_refund_lifecycle_is_not_treated_as_a_transfer() -> None:
    """An expense and its refund are equal and opposite too, but the chain owns them."""
    pair = [
        event("spend", amount=Decimal("500000")),
        event(
            "refund",
            direction=Direction.CREDIT,
            amount=Decimal("500000"),
            event_date=date(2025, 6, 3),
            settlement_date=date(2025, 6, 3),
            linked_event_id="spend",
        ),
    ]
    chained, _ = collapse_chains(pair)
    paired, _ = detect_internal_transfers(pair, chained=chained, transfer=TRANSFER, forecast=FORECAST)
    assert paired == frozenset()


# ---------------------------------------------------------------------------
# Rule 6: lifecycle chains
# ---------------------------------------------------------------------------


def test_chain_collapse_withholds_both_legs_from_projection() -> None:
    pair = [
        event("parent", amount=Decimal("9000"), event_date=date(2025, 5, 20), settlement_date=date(2025, 5, 20)),
        event(
            "child",
            event_type="refund",
            direction=Direction.CREDIT,
            amount=Decimal("9000"),
            event_date=date(2025, 5, 25),
            settlement_date=date(2025, 5, 25),
            linked_event_id="parent",
        ),
    ]
    chained, excluded = collapse_chains(pair)
    assert chained == frozenset({"parent", "child"})
    assert all(x.reason is ExclusionReason.LIFECYCLE_CHAIN for x in excluded)

    ledger = ledger_of(pair, rule=ProjectionRule(selector="trailing_30"))
    assert ledger.flows == ()


# ---------------------------------------------------------------------------
# Rule 7: blank amounts
# ---------------------------------------------------------------------------


def test_blank_amount_is_recorded_and_excluded_never_guessed() -> None:
    blank = event(
        "e_blank",
        amount=None,
        status=EventStatus.PENDING,
        event_date=date(2025, 6, 9),
        settlement_date=date(2025, 6, 11),
    )
    ledger = ledger_of([blank])
    assert ledger.missing_amounts == ("e_blank",)
    assert reasons(ledger)["e_blank"] is ExclusionReason.BLANK_AMOUNT
    assert ledger.flows == ()


def test_supplied_image_amount_is_used_and_clears_the_gap() -> None:
    blank = event(
        "e_blank",
        amount=None,
        status=EventStatus.PENDING,
        event_date=date(2025, 6, 9),
        settlement_date=date(2025, 6, 11),
    )
    ledger = ledger_of([blank], image_amounts={"e_blank": Decimal("4321")})
    assert ledger.missing_amounts == ()
    assert [f.amount for f in ledger.flows] == [Decimal("-4321")]


# ---------------------------------------------------------------------------
# Currency normalisation
# ---------------------------------------------------------------------------


def test_foreign_currency_is_converted_at_ingestion() -> None:
    """A USD 1,800 payroll credit must not be added raw to an IDR balance."""
    usd_salary = event(
        "e_usd",
        direction=Direction.CREDIT,
        amount=Decimal("1800"),
        currency="USD",
        status=EventStatus.SCHEDULED,
        event_date=date(2025, 6, 15),
        settlement_date=date(2025, 6, 15),
    )
    ledger = ledger_of([usd_salary])
    assert len(ledger.flows) == 1
    flow = ledger.flows[0]
    assert flow.amount == Decimal("27000000")
    assert flow.original_amount == Decimal("1800")
    assert flow.original_currency == "USD"


def test_home_currency_events_are_not_converted() -> None:
    ledger = ledger_of(
        [
            event(
                "e1",
                direction=Direction.CREDIT,
                amount=Decimal("1800"),
                currency="IDR",
                status=EventStatus.SCHEDULED,
                event_date=date(2025, 6, 15),
                settlement_date=date(2025, 6, 15),
            )
        ]
    )
    assert ledger.flows[0].amount == Decimal("1800")


# ---------------------------------------------------------------------------
# Selector parsing and projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "family", "days", "occurrences"),
    [
        ("trailing_31", SelectorFamily.TRAILING, 31, 0),
        ("day_stable_2", SelectorFamily.DAY_STABLE, 0, 2),
        ("desc_any_3", SelectorFamily.DESC_ANY, 0, 3),
        ("cat_stable_2", SelectorFamily.CAT_STABLE, 0, 2),
        ("hybrid", SelectorFamily.HYBRID, 0, 0),
    ],
)
def test_selector_names_parse(name, family, days, occurrences) -> None:
    spec = SelectorSpec.parse(name)
    assert spec.family is family
    assert spec.trailing_days == days
    assert spec.min_occurrences == occurrences


def test_unknown_selector_raises() -> None:
    with pytest.raises(ValueError, match="unknown selector"):
        SelectorSpec.parse("wishful_thinking")


def monthly_history(day: int, amount: Decimal, months: int, **overrides):
    return [
        event(
            f"e{i}",
            amount=amount,
            event_date=date(2025, 6 - i, day),
            settlement_date=date(2025, 6 - i, day),
            **overrides,
        )
        for i in range(1, months + 1)
    ]


def test_day_stable_projects_a_repeating_series() -> None:
    ledger = ledger_of(
        monthly_history(5, Decimal("2000"), 3),
        rule=ProjectionRule(selector="day_stable_2", horizon_months=3),
    )
    dates = sorted(f.on_date for f in ledger.flows)
    assert dates == [date(2025, 7, 5), date(2025, 8, 5), date(2025, 9, 5)]
    assert all(f.projected and f.reason is InclusionReason.PROJECTED_RECURRING for f in ledger.flows)


def test_day_stable_ignores_a_series_seen_only_once() -> None:
    ledger = ledger_of(
        monthly_history(5, Decimal("2000"), 1),
        rule=ProjectionRule(selector="day_stable_2", horizon_months=3),
    )
    assert ledger.flows == ()


def test_projection_never_escapes_the_window() -> None:
    ledger = ledger_of(
        monthly_history(5, Decimal("2000"), 3),
        rule=ProjectionRule(selector="day_stable_2", horizon_months=12),
    )
    assert all(ledger.request_date <= f.on_date <= ledger.window_end for f in ledger.flows)


def test_amount_mode_selects_last_mean_or_median() -> None:
    history = [
        event("e1", amount=Decimal("100"), event_date=date(2025, 3, 5), settlement_date=date(2025, 3, 5)),
        event("e2", amount=Decimal("200"), event_date=date(2025, 4, 5), settlement_date=date(2025, 4, 5)),
        event("e3", amount=Decimal("900"), event_date=date(2025, 5, 5), settlement_date=date(2025, 5, 5)),
    ]
    picked = {}
    for mode in AmountMode:
        ledger = ledger_of(
            history, rule=ProjectionRule(selector="day_stable_2", amount_mode=mode, horizon_months=1)
        )
        picked[mode] = -ledger.flows[0].amount

    assert picked[AmountMode.LAST] == Decimal("900")
    assert picked[AmountMode.MEDIAN] == Decimal("200")
    assert picked[AmountMode.MEAN] == Decimal("400")


def test_include_request_date_controls_flows_landing_on_t() -> None:
    on_t = event(
        "e1",
        status=EventStatus.PENDING,
        event_date=REQUEST_DATE,
        settlement_date=REQUEST_DATE,
    )
    assert ledger_of([on_t], rule=ProjectionRule(selector="trailing_30", include_request_date=True)).flows
    assert (
        ledger_of([on_t], rule=ProjectionRule(selector="trailing_30", include_request_date=False)).flows
        == ()
    )


def test_a_scheduled_event_suppresses_the_projection_it_duplicates() -> None:
    """The scheduled row is the truth even when its description differs.

    A scheduled salary is filed as "Next confirmed salary" under a fresh id;
    the history it was inferred from says "Primary household salary". Same date,
    same category, same direction -- counting both doubles the user's income.
    """
    history = [
        event(
            f"h{i}",
            description="Primary household salary",
            category="salary",
            direction=Direction.CREDIT,
            amount=Decimal("5000"),
            event_date=date(2025, 6 - i, 15),
            settlement_date=date(2025, 6 - i, 15),
        )
        for i in (1, 2, 3)
    ]
    scheduled = event(
        "sched",
        description="Next confirmed salary",
        category="salary",
        direction=Direction.CREDIT,
        amount=Decimal("5000"),
        status=EventStatus.SCHEDULED,
        event_date=date(2025, 6, 15),
        settlement_date=date(2025, 6, 15),
    )
    ledger = ledger_of([*history, scheduled], rule=ProjectionRule(selector="day_stable_2"))

    on_that_day = [f for f in ledger.flows if f.on_date == date(2025, 6, 15)]
    assert len(on_that_day) == 1
    assert on_that_day[0].reason is InclusionReason.SCHEDULED_FUTURE


# ---------------------------------------------------------------------------
# Income mode
# ---------------------------------------------------------------------------


def salary_history():
    return [
        event(
            f"s{i}",
            description="Payroll credit",
            category="salary",
            direction=Direction.CREDIT,
            amount=Decimal("5000"),
            event_date=date(2025, 6 - i, 15),
            settlement_date=date(2025, 6 - i, 15),
        )
        for i in (1, 2, 3)
    ]


def test_income_mode_none_projects_no_credits() -> None:
    ledger = ledger_of(
        salary_history(),
        rule=ProjectionRule(selector="day_stable_2", income_mode=IncomeMode.NONE),
    )
    assert ledger.flows == ()


def test_income_mode_all_projects_credits() -> None:
    ledger = ledger_of(
        salary_history(),
        rule=ProjectionRule(selector="day_stable_2", income_mode=IncomeMode.ALL),
    )
    assert all(f.amount > 0 for f in ledger.flows)
    assert len(ledger.flows) == 3


def test_income_mode_stable_keeps_monthly_payroll_but_drops_irregular_payouts() -> None:
    irregular = [
        event(
            f"g{i}",
            description="Delivery platform payout",
            category="gig",
            direction=Direction.CREDIT,
            amount=Decimal("3000"),
            event_date=day,
            settlement_date=day,
        )
        for i, day in enumerate([date(2025, 5, 4), date(2025, 5, 11), date(2025, 5, 18)])
    ]
    ledger = ledger_of(
        [*salary_history(), *irregular],
        rule=ProjectionRule(selector="desc_any_3", income_mode=IncomeMode.STABLE_ONLY),
    )
    descriptions = {f.description for f in ledger.flows}
    assert descriptions == {"Payroll credit"}


def test_income_mode_does_not_touch_debits() -> None:
    ledger = ledger_of(
        monthly_history(5, Decimal("2000"), 3),
        rule=ProjectionRule(selector="day_stable_2", income_mode=IncomeMode.NONE),
    )
    assert len(ledger.flows) == 3
    assert all(f.amount < 0 for f in ledger.flows)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_every_flow_carries_its_source_and_reason() -> None:
    ledger = ledger_of(monthly_history(5, Decimal("2000"), 3), rule=ProjectionRule(selector="day_stable_2"))
    for flow in ledger.flows:
        assert flow.source_event_id
        assert flow.reason in InclusionReason
        assert isinstance(flow.projected, bool)


def test_flows_are_sorted_by_date() -> None:
    ledger = ledger_of(monthly_history(5, Decimal("2000"), 3), rule=ProjectionRule(selector="day_stable_2"))
    dates = [f.on_date for f in ledger.flows]
    assert dates == sorted(dates)
