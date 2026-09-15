"""Options, spending changes, plan ranking, and the end-to-end run."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.capacity import amount_safe_to_pay, earliest_date_for_full_payment
from src.changes import (
    ChangeCandidate,
    apply_changes,
    candidate_changes,
    enumerate_change_sets,
    find_minimal_change_set,
    total_freed,
)
from src.config import DecisionConfig, PlanConfig, default_config
from src.contract import (
    AffordabilityStatus,
    ChangeAction,
    PaymentMethod,
    PaymentPlan,
)
from src.ledger import CashFlow, Direction, EventStatus, InclusionReason, Ledger
from src.options import PaymentOption, eligible_options, full_payment_option
from src.plans import Candidate, RequestRow, decide, is_plan_safe

from tests.test_ledger import event, profile  # reuse the fixtures

REQUEST_DATE = date(2025, 6, 10)
WINDOW_END = REQUEST_DATE + timedelta(days=90)
DECISION = DecisionConfig()
PLAN = PlanConfig()


def ledger(opening: str, *flows: CashFlow, currency: str = "EUR") -> Ledger:
    return Ledger(
        user_id="user_x",
        request_date=REQUEST_DATE,
        window_end=WINDOW_END,
        home_currency=currency,
        opening_balance=Decimal(opening),
        flows=tuple(flows),
    )


def flow(offset: int, amount: str, event_id: str = "e", description: str = "") -> CashFlow:
    return CashFlow(
        on_date=REQUEST_DATE + timedelta(days=offset),
        amount=Decimal(amount),
        source_event_id=event_id,
        reason=InclusionReason.PROJECTED_RECURRING,
        projected=True,
        description=description,
    )


def request(**overrides) -> RequestRow:
    base = {
        "request_id": "request_x",
        "user_id": "user_x",
        "request_date": REQUEST_DATE,
        "request_type": "purchase",
        "requested_amount": Decimal("1000"),
        "desired_completion_date": REQUEST_DATE + timedelta(days=60),
        "allows_partial_payment": True,
        "request_text": "",
    }
    base.update(overrides)
    return RequestRow(**base)


def option(option_id: str, **overrides) -> PaymentOption:
    base = {
        "payment_option_id": option_id,
        "request_id": "request_x",
        "payment_method": PaymentMethod.INSTALLMENTS,
        "payment_amount": Decimal("350"),
        "number_of_payments": 3,
        "first_payment_date": REQUEST_DATE,
        "payment_frequency_days": 30,
        "financing_fee": Decimal("50"),
        "total_payable_amount": Decimal("1050"),
    }
    base.update(overrides)
    return PaymentOption(**base)


# ---------------------------------------------------------------------------
# options.py
# ---------------------------------------------------------------------------


def test_installment_schedule_is_reproduced_verbatim() -> None:
    """first_payment_date + k * frequency, amount = payment_amount."""
    plan = option("o1", first_payment_date=date(2025, 8, 8), payment_frequency_days=30).plan()
    assert plan.serialise() == "2025-08-08:350|2025-09-07:350|2025-10-07:350"


def test_schedule_matches_the_labelled_installment_rows() -> None:
    """The three labelled installment plans, reproduced exactly."""
    cases = [
        (date(2025, 8, 8), 30, 3, Decimal("15952906.67"),
         "2025-08-08:15952906.67|2025-09-07:15952906.67|2025-10-07:15952906.67"),
        (date(2024, 9, 12), 28, 3, Decimal("68432"),
         "2024-09-12:68432|2024-10-10:68432|2024-11-07:68432"),
        (date(2026, 4, 19), 31, 3, Decimal("22590.19"),
         "2026-04-19:22590.19|2026-05-20:22590.19|2026-06-20:22590.19"),
    ]
    for first, freq, count, amount, expected in cases:
        built = option(
            "o", first_payment_date=first, payment_frequency_days=freq,
            number_of_payments=count, payment_amount=amount,
        ).plan()
        assert built.serialise() == expected


def test_full_payment_option_is_a_single_payment() -> None:
    full = option(
        "o1", payment_method=PaymentMethod.FULL_PAYMENT, number_of_payments=1,
        payment_frequency_days=None, payment_amount=Decimal("1000"),
    )
    assert full.plan().serialise() == f"{REQUEST_DATE}:1000"
    assert full_payment_option((full, option("o2"))) is full


def test_max_installment_months_filters_out_the_long_option() -> None:
    """The filter that decides every labelled installments row."""
    short = option("o_short", number_of_payments=3, payment_frequency_days=30)
    long = option("o_long", number_of_payments=18, payment_frequency_days=31)
    full = option(
        "o_full", payment_method=PaymentMethod.FULL_PAYMENT, number_of_payments=1,
        payment_frequency_days=None,
    )
    prof = profile(
        payment_methods_user_will_consider=("partial_payment", "installments"),
        max_installment_months=7,
    )
    kept = eligible_options((short, full, long), prof, days_per_month=30)
    assert [o.payment_option_id for o in kept] == ["o_short"]


def test_blank_max_installment_months_removes_every_installment_option() -> None:
    prof = profile(
        payment_methods_user_will_consider=("full_payment", "installments"),
        max_installment_months=None,
    )
    kept = eligible_options((option("o1"),), prof, days_per_month=30)
    assert kept == ()


def test_a_method_the_user_rejects_is_never_eligible() -> None:
    full = option("o_full", payment_method=PaymentMethod.FULL_PAYMENT, payment_frequency_days=None)
    prof = profile(payment_methods_user_will_consider=("installments",), max_installment_months=12)
    assert [o.payment_option_id for o in eligible_options((full, option("o1")), prof, days_per_month=30)] == ["o1"]


# ---------------------------------------------------------------------------
# changes.py
# ---------------------------------------------------------------------------


def flexible_profile(**overrides):
    base = {
        "expense_categories_user_is_willing_to_reduce": ("dining",),
        "expense_categories_user_is_willing_to_stop": ("streaming",),
        "expense_categories_to_protect": ("rent",),
    }
    base.update(overrides)
    return profile(**base)


def test_fixed_events_are_never_changeable() -> None:
    events = [event("e1", flexibility="fixed", category="streaming")]
    assert candidate_changes(flexible_profile(), events, DECISION) == ()


def test_protected_categories_are_never_changeable() -> None:
    events = [event("e1", flexibility="stoppable", category="rent")]
    assert candidate_changes(flexible_profile(), events, DECISION) == ()


def test_a_category_the_user_did_not_offer_is_not_changeable() -> None:
    """Flexible in the data, but the user never said they would stop it."""
    events = [event("e1", flexibility="stoppable", category="gym")]
    assert candidate_changes(flexible_profile(), events, DECISION) == ()


def test_stop_and_reduce_candidates_come_from_flexibility_and_preference() -> None:
    events = [
        event("e_stop", flexibility="stoppable", category="streaming", description="Streaming"),
        event(
            "e_reduce", flexibility="reducible", category="dining", description="Dining",
            amount=Decimal("100"), minimum_allowed_amount=Decimal("40"),
        ),
    ]
    actions = {(c.event_id, c.action) for c in candidate_changes(flexible_profile(), events, DECISION)}
    assert actions == {("e_stop", ChangeAction.STOP), ("e_reduce", ChangeAction.REDUCE_TO)}


def test_reduce_to_uses_the_minimum_allowed_amount_as_its_floor() -> None:
    events = [
        event(
            "e1", flexibility="reducible", category="dining", amount=Decimal("100"),
            minimum_allowed_amount=Decimal("23.5"),
        )
    ]
    candidate = candidate_changes(flexible_profile(), events, DECISION)[0]
    assert candidate.new_amount == Decimal("23.5")
    assert candidate.to_spending_change().serialise() == "reduce_to:e1:23.50"


def test_the_most_recent_event_of_a_series_is_the_one_cited() -> None:
    events = [
        event(f"e{i}", flexibility="stoppable", category="streaming", description="Streaming",
              event_date=date(2025, m, 5), settlement_date=date(2025, m, 5))
        for i, m in enumerate((3, 4, 5), start=1)
    ]
    candidates = candidate_changes(flexible_profile(), events, DECISION)
    assert [c.event_id for c in candidates] == ["e3"]


def test_stop_removes_every_occurrence_and_reduce_scales_them() -> None:
    book = ledger("1000", flow(5, "-50", "e1", "Streaming"), flow(35, "-50", "e1", "Streaming"))
    stop = ChangeCandidate(
        event_id="e1", action=ChangeAction.STOP, description="Streaming",
        category="streaming", original_amount=Decimal("50"), new_amount=None,
    )
    assert apply_changes(book, [stop]).flows == ()
    assert total_freed(book, [stop]) == Decimal("100")

    reduce = ChangeCandidate(
        event_id="e1", action=ChangeAction.REDUCE_TO, description="Streaming",
        category="streaming", original_amount=Decimal("50"), new_amount=Decimal("20"),
    )
    assert [f.amount for f in apply_changes(book, [reduce]).flows] == [Decimal("-20"), Decimal("-20")]


def test_one_change_can_free_its_amount_across_several_cycles() -> None:
    """The trough may be several billing cycles out -- verified by request_11."""
    book = ledger("1000", *(flow(30 * k, "-100", "e1", "Sub") for k in (1, 2, 3)))
    stop = ChangeCandidate(
        event_id="e1", action=ChangeAction.STOP, description="Sub",
        category="streaming", original_amount=Decimal("100"), new_amount=None,
    )
    assert total_freed(book, [stop]) == Decimal("300")


def test_changes_never_touch_credits() -> None:
    book = ledger("1000", flow(5, "500", "e1", "Salary"))
    stop = ChangeCandidate(
        event_id="e1", action=ChangeAction.STOP, description="Salary",
        category="streaming", original_amount=Decimal("500"), new_amount=None,
    )
    assert apply_changes(book, [stop]).flows == book.flows


def test_enumeration_respects_the_cap_and_the_stop_reduce_conflict() -> None:
    made = [
        ChangeCandidate(
            event_id="e1", action=ChangeAction.STOP, description="A", category="streaming",
            original_amount=Decimal("10"), new_amount=None,
        ),
        ChangeCandidate(
            event_id="e1", action=ChangeAction.REDUCE_TO, description="A", category="streaming",
            original_amount=Decimal("10"), new_amount=Decimal("5"),
        ),
    ]
    subsets = list(enumerate_change_sets(made, 3))
    assert () in subsets
    assert all(len({c.event_id for c in s}) == len(s) for s in subsets)
    assert max(len(s) for s in subsets) == 1


def test_search_prefers_smallest_total_freed_over_fewest_changes() -> None:
    """request_21's label uses TWO changes freeing 34.50 over ONE freeing 47."""
    book = ledger("1000", flow(5, "-11", "small", "Backup"), flow(5, "-47", "big", "Streaming"))
    small_stop = ChangeCandidate(
        event_id="small", action=ChangeAction.STOP, description="Backup",
        category="streaming", original_amount=Decimal("11"), new_amount=None,
    )
    big_stop = ChangeCandidate(
        event_id="big", action=ChangeAction.STOP, description="Streaming",
        category="streaming", original_amount=Decimal("47"), new_amount=None,
    )
    big_reduce = ChangeCandidate(
        event_id="big", action=ChangeAction.REDUCE_TO, description="Streaming",
        category="streaming", original_amount=Decimal("47"), new_amount=Decimal("23.50"),
    )
    # Need 31.05 freed. big_stop alone gives 47 in one change;
    # small_stop + big_reduce give 34.50 in two. The smaller total must win.
    chosen = find_minimal_change_set(
        book, [small_stop, big_stop, big_reduce], 3,
        lambda adjusted: total_freed(book, []) is not None
        and min(p.balance for p in __import__("src.capacity", fromlist=["x"]).balance_path(adjusted))
        >= Decimal("1000") - Decimal("58") + Decimal("31.05"),
    )
    assert chosen is not None
    assert sorted(c.event_id for c in chosen) == ["big", "small"]
    assert total_freed(book, chosen) == Decimal("34.50")


def test_search_returns_none_when_nothing_is_enough() -> None:
    book = ledger("1000", flow(5, "-10", "e1", "Tiny"))
    tiny = ChangeCandidate(
        event_id="e1", action=ChangeAction.STOP, description="Tiny",
        category="streaming", original_amount=Decimal("10"), new_amount=None,
    )
    assert find_minimal_change_set(book, [tiny], 3, lambda _adjusted: False) is None


# ---------------------------------------------------------------------------
# plans.py
# ---------------------------------------------------------------------------


def test_plan_safety_rejects_a_plan_that_breaches_the_minimum() -> None:
    book = ledger("1000")
    assert is_plan_safe(book, PaymentPlan.of([(REQUEST_DATE, Decimal("500"))]), Decimal("400"), PLAN)
    assert not is_plan_safe(
        book, PaymentPlan.of([(REQUEST_DATE, Decimal("700"))]), Decimal("400"), PLAN
    )


def test_plan_safety_extends_past_the_window_without_assuming_more_income() -> None:
    """A plan outliving the forecast gets no invented income out there.

    Balance runs 1000 -> 400 (pay 600) -> 900 (the one projected credit) and
    then stops rising, because nothing supports income past `window_end`. A 950
    payment at day 120 therefore breaches, even though another month of the same
    credit would have covered it.
    """
    book = ledger("1000", flow(10, "500"))
    beyond = PaymentPlan.of(
        [(REQUEST_DATE, Decimal("600")), (REQUEST_DATE + timedelta(days=120), Decimal("950"))]
    )
    assert not is_plan_safe(book, beyond, Decimal("0"), PLAN)

    within_means = PaymentPlan.of(
        [(REQUEST_DATE, Decimal("600")), (REQUEST_DATE + timedelta(days=120), Decimal("900"))]
    )
    assert is_plan_safe(book, within_means, Decimal("0"), PLAN)


def test_ranking_puts_total_paid_above_starting_earlier() -> None:
    """Rule 3 beats rule 4: a fee-free plan outranks a cheaper-starting one."""
    cheap_late = Candidate(
        method=PaymentMethod.WAIT,
        plan=PaymentPlan.of([(REQUEST_DATE + timedelta(days=30), Decimal("1000"))]),
        total_paid=Decimal("1000"),
        completes_by_deadline=True,
    )
    dear_early = Candidate(
        method=PaymentMethod.INSTALLMENTS,
        plan=PaymentPlan.of([(REQUEST_DATE, Decimal("350"))]),
        total_paid=Decimal("1050"),
        completes_by_deadline=True,
    )
    assert min([dear_early, cheap_late], key=lambda c: c.rank_key) is cheap_late


def test_ranking_prefers_no_spending_changes() -> None:
    clean = Candidate(
        method=PaymentMethod.INSTALLMENTS,
        plan=PaymentPlan.of([(REQUEST_DATE, Decimal("1000"))]),
        total_paid=Decimal("1000"), completes_by_deadline=True,
    )
    dirty = Candidate(
        method=PaymentMethod.FULL_PAYMENT,
        plan=PaymentPlan.of([(REQUEST_DATE, Decimal("900"))]),
        total_paid=Decimal("900"), completes_by_deadline=True,
        changes=(ChangeCandidate(
            event_id="e1", action=ChangeAction.STOP, description="A",
            category="streaming", original_amount=Decimal("5"), new_amount=None,
        ),),
    )
    assert min([dirty, clean], key=lambda c: c.rank_key) is clean


def test_meeting_the_deadline_outranks_everything_else() -> None:
    late_free = Candidate(
        method=PaymentMethod.WAIT,
        plan=PaymentPlan.of([(REQUEST_DATE + timedelta(days=80), Decimal("1000"))]),
        total_paid=Decimal("1000"), completes_by_deadline=False,
    )
    timely_dear = Candidate(
        method=PaymentMethod.INSTALLMENTS,
        plan=PaymentPlan.of([(REQUEST_DATE, Decimal("2000"))]),
        total_paid=Decimal("2000"), completes_by_deadline=True,
    )
    assert min([late_free, timely_dear], key=lambda c: c.rank_key) is timely_dear


def decide_with(book: Ledger, prof, req: RequestRow, options=(), changes=()):
    amount_safe = amount_safe_to_pay(book, prof.minimum_balance_to_keep, req.requested_amount)
    earliest = earliest_date_for_full_payment(
        book, prof.minimum_balance_to_keep, req.requested_amount, book.window_end
    )
    return decide(
        req, prof, book, options, changes, amount_safe, earliest,
        decision=DECISION, plan_config=PLAN,
    )


def test_status_rule_1_full_capacity_and_the_user_pays_in_full() -> None:
    prof = profile(
        minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=("full_payment",),
    )
    result = decide_with(ledger("5000"), prof, request())
    assert result.status_rule == 1
    assert result.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
    assert result.recommended_payment_method is PaymentMethod.FULL_PAYMENT
    assert result.earliest_date_for_full_payment == REQUEST_DATE


def test_full_capacity_but_the_user_rejects_full_payment_is_not_affordable_now() -> None:
    """Capacity today is not enough; the user has to accept the method."""
    prof = profile(
        minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=("installments",),
        max_installment_months=12,
    )
    result = decide_with(ledger("5000"), prof, request(), options=(option("o1"),))
    assert result.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW


def test_status_rule_3_defers_to_wait() -> None:
    prof = profile(
        minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=("full_payment",),
    )
    book = ledger("500", flow(40, "3000"))
    result = decide_with(book, prof, request(desired_completion_date=None))
    assert result.status_rule == 3
    assert result.recommended_payment_method is PaymentMethod.WAIT
    assert len(result.payment_plan) == 1
    assert result.payment_plan.entries[0].amount == Decimal("1000")


def test_status_rule_5_when_nothing_works() -> None:
    prof = profile(
        minimum_balance_to_keep=Decimal("900"),
        payment_methods_user_will_consider=("full_payment",),
    )
    result = decide_with(ledger("1000"), prof, request())
    assert result.status_rule == 5
    assert result.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert result.recommended_payment_method is PaymentMethod.NOT_RECOMMENDED
    assert result.payment_plan.serialise() == "none"


def test_status_rule_4_is_logged_loudly_when_it_fires() -> None:
    """Unvalidated branch: never seen in the labels, so it must announce itself."""
    prof = profile(
        minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=("partial_payment",),
    )
    book = ledger("500", flow(40, "3000"))
    result = decide_with(
        book, prof, request(allows_partial_payment=False, desired_completion_date=None)
    )
    assert result.status_rule == 4
    assert result.affordability_status is AffordabilityStatus.AFFORDABLE_LATER
    assert result.recommended_payment_method is PaymentMethod.NOT_RECOMMENDED
    assert any("STATUS RULE 4 FIRED" in note for note in result.notes)


def test_partial_payment_is_two_payments_summing_to_the_request() -> None:
    prof = profile(
        minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=("partial_payment",),
    )
    book = ledger("700", flow(20, "2000"))
    result = decide_with(book, prof, request())
    assert result.recommended_payment_method is PaymentMethod.PARTIAL_PAYMENT
    assert len(result.payment_plan) == 2
    assert result.payment_plan.total == Decimal("1000")
    assert result.payment_plan.entries[0].due_date == REQUEST_DATE


def test_partial_payment_needs_the_request_to_allow_it() -> None:
    prof = profile(
        minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=("partial_payment",),
    )
    book = ledger("700", flow(20, "2000"))
    result = decide_with(book, prof, request(allows_partial_payment=False))
    assert result.recommended_payment_method is not PaymentMethod.PARTIAL_PAYMENT


def test_capacity_is_reported_from_the_baseline_even_when_a_change_is_used() -> None:
    """Requests 06, 11 and 21: earliest may fall AFTER the deadline while a
    spending change makes a full payment safe today. The reported capacity must
    stay on the unchanged ledger."""
    prof = profile(
        minimum_balance_to_keep=Decimal("500"),
        payment_methods_user_will_consider=("full_payment",),
        expense_categories_user_is_willing_to_stop=("streaming",),
    )
    book = ledger("1550", flow(5, "-100", "e_sub", "Streaming"))
    change = ChangeCandidate(
        event_id="e_sub", action=ChangeAction.STOP, description="Streaming",
        category="streaming", original_amount=Decimal("100"), new_amount=None,
    )
    baseline_safe = amount_safe_to_pay(book, Decimal("500"), Decimal("1000"))
    result = decide_with(book, prof, request(), changes=(change,))

    assert result.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN
    assert result.recommended_payment_method is PaymentMethod.FULL_PAYMENT
    assert result.spending_changes.serialise() == "stop:e_sub"
    assert result.amount_safe_to_pay == baseline_safe == Decimal("950")


# ---------------------------------------------------------------------------
# run.py, end to end
# ---------------------------------------------------------------------------


def test_end_to_end_writes_one_validated_row_per_request(tmp_path) -> None:
    from src.run import run

    cfg = default_config()
    out = tmp_path / "output.csv"
    rows, summary = run(
        cfg,
        requests_path=cfg.paths.sample_requests_csv,
        output_path=out,
        trace_path=tmp_path / "trace.jsonl",
    )
    assert summary.rows == 25
    assert len(rows) == 25
    assert summary.fallbacks == ()
    for row in rows:
        row.validate_contract()
    assert out.exists()
    assert (tmp_path / "trace.jsonl").read_text(encoding="utf-8").count("\n") == 25


def test_end_to_end_preserves_input_order(tmp_path) -> None:
    from src.run import load_requests, run

    cfg = default_config()
    rows, _ = run(
        cfg,
        requests_path=cfg.paths.sample_requests_csv,
        output_path=tmp_path / "o.csv",
        trace_path=tmp_path / "t.jsonl",
    )
    expected = [r.request_id for r in load_requests(cfg.paths.sample_requests_csv)]
    assert [r.request_id for r in rows] == expected
