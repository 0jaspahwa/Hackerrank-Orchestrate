"""The scorer must survive a useless predictor and a broken one."""

from __future__ import annotations

import pytest

from evaluation.score import (
    compare_rows,
    load_labelled_requests,
    score,
    stub_predictor,
    tally_categoricals,
    tally_numeric,
)
from src.config import default_config


def test_labelled_rows_load_with_labels_attached() -> None:
    requests = load_labelled_requests(default_config())
    assert len(requests) == 25
    first = requests[0]
    assert first.request_id.startswith("request_")
    assert first.requested_amount > 0
    assert first.expected["affordability_status"] != ""


def test_stub_predictor_scores_without_crashing(capsys: pytest.CaptureFixture[str]) -> None:
    comparisons = score(stub_predictor, label="stub")
    captured = capsys.readouterr().out

    assert len(comparisons) == 25
    assert all(c.error is None for c in comparisons)
    assert "PER-FIELD ACCURACY" in captured
    assert "PER-ROW" in captured
    assert "DECISION EXPLANATIONS" in captured
    assert "n=25" in captured
    assert "4.0 pp" in captured
    assert "CONFUSION MATRIX" in captured


def test_stub_matches_the_not_affordable_rows_and_misses_the_rest() -> None:
    requests = load_labelled_requests(default_config())
    comparisons = compare_rows(requests, stub_predictor)
    tallies = tally_categoricals(comparisons)

    status = tallies["affordability_status"]
    assert 0 < status.hits < status.total, "stub should hit the not_affordable rows only"
    assert tallies["payment_plan"].hits == status.hits


def test_numeric_tally_reports_error_not_just_accuracy() -> None:
    requests = load_labelled_requests(default_config())
    numeric = tally_numeric(compare_rows(requests, stub_predictor))

    assert numeric.total == 25
    assert numeric.mean_absolute_error > 0, "predicting 0 everywhere must show error"
    assert numeric.median_relative_error > 0


def test_a_raising_predictor_is_reported_not_propagated(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(_request: object) -> None:
        raise RuntimeError("boom")

    comparisons = score(broken, limit=3, label="broken")  # type: ignore[arg-type]
    captured = capsys.readouterr().out

    assert len(comparisons) == 3
    assert all(c.error == "RuntimeError: boom" for c in comparisons)
    assert "row(s) raised" in captured
