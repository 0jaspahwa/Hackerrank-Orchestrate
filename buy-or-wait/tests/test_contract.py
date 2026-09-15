"""Contract tests.

Every invariant is asserted BOTH ways: a valid row passes, and a row violating
that one invariant raises. Nothing here needs an API key or the network.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.contract import (
    OUTPUT_COLUMNS,
    AffordabilityStatus,
    ChangeAction,
    ContractError,
    OutputRow,
    PaymentEntry,
    PaymentMethod,
    PaymentPlan,
    SpendingChange,
    SpendingChangeSet,
    format_plan_amount,
    format_safe_amount,
    quantize_down,
    safe_default_row,
    write_output_csv,
)

REQUEST_DATE = date(2025, 8, 5)
REQUESTED = Decimal("1000")


def make_row(**overrides: object) -> OutputRow:
    """An affordable_now row that satisfies every invariant, minus overrides."""
    base: dict[str, object] = {
        "request_id": "request_01",
        "amount_safe_to_pay": REQUESTED,
        "affordability_status": AffordabilityStatus.AFFORDABLE_NOW,
        "recommended_payment_method": PaymentMethod.FULL_PAYMENT,
        "payment_plan": PaymentPlan.of([(REQUEST_DATE, REQUESTED)]),
        "earliest_date_for_full_payment": REQUEST_DATE,
        "spending_changes_needed": SpendingChangeSet.empty(),
        "decision_explanation": "Pay the full amount today.",
        "request_date": REQUEST_DATE,
        "requested_amount": REQUESTED,
    }
    base.update(overrides)
    return OutputRow(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------


def test_contract_imports_without_api_key() -> None:
    """A clean interpreter with no API key must import the contract.

    Checked in a subprocess: reloading the module in-process would rebind its
    classes and invalidate every model already imported by this test file.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", "import src.contract as c; print(c.OUTPUT_COLUMNS[0])"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "request_id"


def test_column_order_is_exactly_the_spec() -> None:
    assert OUTPUT_COLUMNS == (
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    )


def test_enum_members_are_the_closed_sets() -> None:
    assert {s.value for s in AffordabilityStatus} == {
        "affordable_now",
        "affordable_with_plan",
        "affordable_later",
        "not_affordable",
    }
    assert {m.value for m in PaymentMethod} == {
        "full_payment",
        "partial_payment",
        "installments",
        "wait",
        "not_recommended",
    }


# ---------------------------------------------------------------------------
# Formatters -- the literals here are taken from dataset/sample_requests.csv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("17229139.2"), "17229139.2"),
        (Decimal("603.3"), "603.3"),
        (Decimal("462"), "462"),
        (Decimal("462.00"), "462"),
        (Decimal("87170.56"), "87170.56"),
        (Decimal("8401800"), "8401800"),
        (Decimal("0"), "0"),
        (Decimal("0.00"), "0"),
        (Decimal("4620"), "4620"),
    ],
)
def test_format_safe_amount_strips_trailing_zeros(value: Decimal, expected: str) -> None:
    assert format_safe_amount(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("620.4"), "620.40"),
        (Decimal("996.6"), "996.60"),
        (Decimal("3246.1"), "3246.10"),
        (Decimal("15952906.67"), "15952906.67"),
        (Decimal("23.5"), "23.50"),
        (Decimal("25256"), "25256"),
        (Decimal("68432"), "68432"),
        (Decimal("665950"), "665950"),
        (Decimal("13110000"), "13110000"),
    ],
)
def test_format_plan_amount_two_decimals_only_when_fractional(
    value: Decimal, expected: str
) -> None:
    assert format_plan_amount(value) == expected


def test_the_two_formatters_differ_on_the_same_value() -> None:
    """620.4 is '620.4' as a safe amount and '620.40' in a plan. Do not unify."""
    assert format_safe_amount(Decimal("620.4")) == "620.4"
    assert format_plan_amount(Decimal("620.4")) == "620.40"


def test_safe_amount_rounds_down_never_up() -> None:
    assert quantize_down(Decimal("100.999")) == Decimal("100.99")
    assert quantize_down(Decimal("100.005")) == Decimal("100.00")


# ---------------------------------------------------------------------------
# Plan and change serialisation
# ---------------------------------------------------------------------------


def test_plan_serialises_to_the_pipe_format() -> None:
    plan = PaymentPlan.of(
        [
            (date(2025, 8, 8), Decimal("15952906.67")),
            (date(2025, 9, 7), Decimal("15952906.67")),
            (date(2025, 10, 7), Decimal("15952906.67")),
        ]
    )
    assert plan.serialise() == (
        "2025-08-08:15952906.67|2025-09-07:15952906.67|2025-10-07:15952906.67"
    )
    assert PaymentPlan.parse(plan.serialise()) == plan


def test_empty_plan_serialises_to_none_literal() -> None:
    assert PaymentPlan.empty().serialise() == "none"
    assert PaymentPlan.parse("none") == PaymentPlan.empty()
    assert PaymentPlan.parse("") == PaymentPlan.empty()


def test_plan_must_be_chronological() -> None:
    PaymentPlan.of([(date(2025, 1, 1), 1), (date(2025, 2, 1), 1)])  # passes
    with pytest.raises(ValueError, match="chronological"):
        PaymentPlan.of([(date(2025, 2, 1), 1), (date(2025, 1, 1), 1)])


def test_plan_entry_amount_must_be_positive() -> None:
    PaymentEntry(due_date=REQUEST_DATE, amount=Decimal("0.01"))  # passes
    with pytest.raises(ValueError, match="must be > 0"):
        PaymentEntry(due_date=REQUEST_DATE, amount=Decimal("0"))


def test_spending_change_serialisation_round_trips() -> None:
    stop = SpendingChange.stop("event_476")
    reduce = SpendingChange.reduce_to("event_1816", Decimal("23.5"))
    assert stop.serialise() == "stop:event_476"
    assert reduce.serialise() == "reduce_to:event_1816:23.50"

    changes = SpendingChangeSet(changes=(stop, reduce))
    assert changes.serialise() == "stop:event_476|reduce_to:event_1816:23.50"
    assert SpendingChangeSet.parse(changes.serialise()) == changes


def test_reduce_to_integral_amount_has_no_decimals() -> None:
    assert SpendingChange.reduce_to("event_989", 665950).serialise() == "reduce_to:event_989:665950"


def test_empty_change_set_serialises_to_none_literal() -> None:
    assert SpendingChangeSet.empty().serialise() == "none"
    assert SpendingChangeSet.parse("none") == SpendingChangeSet.empty()


def test_stop_rejects_an_amount_and_reduce_to_requires_one() -> None:
    SpendingChange.stop("event_1")  # passes
    with pytest.raises(ValueError, match="must not carry"):
        SpendingChange(action=ChangeAction.STOP, event_id="event_1", new_amount=Decimal(5))
    with pytest.raises(ValueError, match="requires a new_amount"):
        SpendingChange(action=ChangeAction.REDUCE_TO, event_id="event_1")


# ---------------------------------------------------------------------------
# Invariants -- each asserted both ways
# ---------------------------------------------------------------------------


def test_valid_row_passes_validation() -> None:
    assert make_row().validate_contract() is not None


def test_validate_alias_is_the_same_check() -> None:
    row = make_row()
    assert row.validate() is row.validate_contract()


def test_amount_safe_to_pay_within_zero_and_requested() -> None:
    make_row(amount_safe_to_pay=Decimal("0")).validate_contract()
    make_row(amount_safe_to_pay=REQUESTED).validate_contract()

    with pytest.raises(ContractError, match="< 0"):
        make_row(
            amount_safe_to_pay=Decimal("-1"),
            affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
            recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
            payment_plan=PaymentPlan.empty(),
            earliest_date_for_full_payment=None,
        ).validate_contract()

    with pytest.raises(ContractError, match="> requested_amount"):
        make_row(amount_safe_to_pay=REQUESTED + Decimal("1")).validate_contract()


def test_affordable_now_requires_earliest_equal_to_request_date() -> None:
    make_row(earliest_date_for_full_payment=REQUEST_DATE).validate_contract()

    with pytest.raises(ContractError, match="affordable_now requires"):
        make_row(earliest_date_for_full_payment=date(2025, 9, 1)).validate_contract()

    with pytest.raises(ContractError, match="affordable_now requires"):
        make_row(earliest_date_for_full_payment=None).validate_contract()


def partial_row(**overrides: object) -> OutputRow:
    base: dict[str, object] = {
        "amount_safe_to_pay": Decimal("400"),
        "affordability_status": AffordabilityStatus.AFFORDABLE_WITH_PLAN,
        "recommended_payment_method": PaymentMethod.PARTIAL_PAYMENT,
        "payment_plan": PaymentPlan.of(
            [(REQUEST_DATE, Decimal("400")), (date(2025, 9, 15), Decimal("600"))]
        ),
        "earliest_date_for_full_payment": date(2025, 9, 15),
    }
    base.update(overrides)
    return make_row(**base)


def test_partial_payment_requires_affordable_with_plan() -> None:
    partial_row().validate_contract()

    with pytest.raises(ContractError, match="requires affordable_with_plan"):
        partial_row(affordability_status=AffordabilityStatus.AFFORDABLE_LATER).validate_contract()


def test_partial_payment_requires_exactly_two_payments() -> None:
    partial_row().validate_contract()

    with pytest.raises(ContractError, match="exactly 2 payments"):
        partial_row(
            payment_plan=PaymentPlan.of([(REQUEST_DATE, Decimal("1000"))])
        ).validate_contract()

    with pytest.raises(ContractError, match="exactly 2 payments"):
        partial_row(
            payment_plan=PaymentPlan.of(
                [
                    (REQUEST_DATE, Decimal("400")),
                    (date(2025, 9, 1), Decimal("300")),
                    (date(2025, 9, 15), Decimal("300")),
                ]
            )
        ).validate_contract()


def test_partial_payment_payments_must_sum_to_requested_amount() -> None:
    partial_row().validate_contract()

    with pytest.raises(ContractError, match="plan totals"):
        partial_row(
            payment_plan=PaymentPlan.of(
                [(REQUEST_DATE, Decimal("400")), (date(2025, 9, 15), Decimal("599"))]
            )
        ).validate_contract()


def test_not_recommended_requires_an_empty_plan() -> None:
    safe_default_row("request_05", request_date=REQUEST_DATE, requested_amount=REQUESTED).validate_contract()

    with pytest.raises(ContractError, match="requires payment_plan 'none'"):
        make_row(
            amount_safe_to_pay=Decimal("0"),
            affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
            recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
            earliest_date_for_full_payment=None,
            payment_plan=PaymentPlan.of([(REQUEST_DATE, REQUESTED)]),
        ).validate_contract()


def test_no_event_may_be_both_stopped_and_reduced() -> None:
    make_row(
        spending_changes_needed=SpendingChangeSet(
            changes=(SpendingChange.stop("event_1"), SpendingChange.reduce_to("event_2", 10))
        )
    ).validate_contract()

    with pytest.raises(ContractError, match="both stopped and reduced"):
        make_row(
            spending_changes_needed=SpendingChangeSet(
                changes=(SpendingChange.stop("event_1"), SpendingChange.reduce_to("event_1", 10))
            )
        ).validate_contract()


def test_at_most_three_spending_changes() -> None:
    three = SpendingChangeSet(
        changes=(
            SpendingChange.stop("event_1"),
            SpendingChange.stop("event_2"),
            SpendingChange.reduce_to("event_3", 10),
        )
    )
    make_row(spending_changes_needed=three).validate_contract()

    four = SpendingChangeSet(changes=(*three.changes, SpendingChange.stop("event_4")))
    with pytest.raises(ContractError, match="exceeds the maximum"):
        make_row(spending_changes_needed=four).validate_contract()


def test_to_csv_dict_validates_before_emitting() -> None:
    with pytest.raises(ContractError):
        make_row(earliest_date_for_full_payment=None).to_csv_dict()


# ---------------------------------------------------------------------------
# SAFE_DEFAULT
# ---------------------------------------------------------------------------


def test_safe_default_shape() -> None:
    row = safe_default_row("request_99")
    emitted = row.to_csv_dict()
    assert emitted["amount_safe_to_pay"] == "0"
    assert emitted["affordability_status"] == "not_affordable"
    assert emitted["recommended_payment_method"] == "not_recommended"
    assert emitted["payment_plan"] == "none"
    assert emitted["earliest_date_for_full_payment"] == ""
    assert emitted["spending_changes_needed"] == "none"
    assert "enough verified information" in emitted["decision_explanation"]


def test_safe_default_round_trips_through_a_csv_line() -> None:
    original = safe_default_row(
        "request_99", request_date=REQUEST_DATE, requested_amount=REQUESTED
    )
    line = original.to_csv_line()

    parsed_fields = next(csv.reader(io.StringIO(line)))
    assert len(parsed_fields) == len(OUTPUT_COLUMNS)

    row_dict = dict(zip(OUTPUT_COLUMNS, parsed_fields))
    restored = OutputRow.from_csv_dict(
        row_dict, request_date=REQUEST_DATE, requested_amount=REQUESTED
    )

    assert restored.to_csv_dict() == original.to_csv_dict()
    assert restored.amount_safe_to_pay == original.amount_safe_to_pay
    assert restored.affordability_status is original.affordability_status
    assert restored.recommended_payment_method is original.recommended_payment_method
    assert restored.payment_plan == original.payment_plan
    assert restored.earliest_date_for_full_payment is None
    assert restored.spending_changes_needed == original.spending_changes_needed


def test_rich_row_round_trips_through_a_csv_line() -> None:
    original = make_row(
        amount_safe_to_pay=Decimal("603.3"),
        affordability_status=AffordabilityStatus.AFFORDABLE_WITH_PLAN,
        earliest_date_for_full_payment=date(2026, 1, 15),
        payment_plan=PaymentPlan.of([(REQUEST_DATE, Decimal("620.4"))]),
        spending_changes_needed=SpendingChangeSet(changes=(SpendingChange.stop("event_476"),)),
        requested_amount=Decimal("620.4"),
    )
    fields = next(csv.reader(io.StringIO(original.to_csv_line())))
    restored = OutputRow.from_csv_dict(
        dict(zip(OUTPUT_COLUMNS, fields)),
        request_date=REQUEST_DATE,
        requested_amount=Decimal("620.4"),
    )
    assert restored.to_csv_dict() == original.to_csv_dict()


def test_write_output_csv_header_and_rows(tmp_path) -> None:
    target = tmp_path / "output.csv"
    write_output_csv(str(target), [safe_default_row("request_26"), safe_default_row("request_27")])

    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == list(OUTPUT_COLUMNS)
    assert len(rows) == 3
    assert rows[1][0] == "request_26"
