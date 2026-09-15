"""Exchange-rate resolution: identity, direct, inverse, pivot, nearest date."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.config import default_config
from src.fx import (
    FXError,
    RateQuote,
    build_rate_table,
    convert,
    convert_with_trace,
    find_path,
    load_rates,
)

MAX_HOPS = 3
D = date(2024, 1, 15)


def table():
    """The dataset's five directed pairs, one quote date, round numbers."""
    return build_rate_table(
        [
            RateQuote(rate_date=D, from_currency="EUR", to_currency="USD", rate=Decimal("1.10")),
            RateQuote(rate_date=D, from_currency="EUR", to_currency="ZAR", rate=Decimal("20")),
            RateQuote(rate_date=D, from_currency="USD", to_currency="EUR", rate=Decimal("0.92")),
            RateQuote(rate_date=D, from_currency="USD", to_currency="IDR", rate=Decimal("15000")),
            RateQuote(rate_date=D, from_currency="USD", to_currency="INR", rate=Decimal("83")),
        ]
    )


def test_identity_returns_unchanged_without_touching_the_table() -> None:
    empty = build_rate_table([])
    assert convert(Decimal("123.45"), "JPY", "JPY", D, empty, max_hops=MAX_HOPS) == Decimal("123.45")


def test_direct_pair() -> None:
    assert convert(Decimal("100"), "USD", "INR", D, table(), max_hops=MAX_HOPS) == Decimal("8300")


def test_derived_inverse() -> None:
    """ZAR->EUR is not quoted; it must come from 1/(EUR->ZAR)."""
    result = convert_with_trace(Decimal("2000"), "ZAR", "EUR", D, table(), max_hops=MAX_HOPS)
    assert len(result.hops) == 1
    assert result.hops[0].inverted is True
    assert result.amount == Decimal("100")


def test_direct_quote_is_preferred_over_inverting_the_reverse() -> None:
    """USD->EUR is quoted at 0.92; 1/1.10 would be 0.909. The quote wins."""
    result = convert_with_trace(Decimal("100"), "USD", "EUR", D, table(), max_hops=MAX_HOPS)
    assert result.hops[0].inverted is False
    assert result.amount == Decimal("92.00")


def test_two_hop_pivot() -> None:
    """IDR->EUR has no direct or reverse quote; it pivots through USD."""
    result = convert_with_trace(Decimal("15000"), "IDR", "EUR", D, table(), max_hops=MAX_HOPS)
    assert [(h.from_currency, h.to_currency) for h in result.hops] == [
        ("IDR", "USD"),
        ("USD", "EUR"),
    ]
    assert result.amount == Decimal("0.92")


def test_three_hop_pivot_zar_to_inr() -> None:
    """The longest real path: ZAR -> EUR -> USD -> INR."""
    path = find_path(table(), "ZAR", "INR", max_hops=MAX_HOPS)
    assert path == ("ZAR", "EUR", "USD", "INR")


def test_path_longer_than_max_hops_raises() -> None:
    with pytest.raises(FXError, match="within 2 hops"):
        find_path(table(), "ZAR", "INR", max_hops=2)


def test_unknown_currency_raises() -> None:
    with pytest.raises(FXError, match="does not appear"):
        convert(Decimal("1"), "JPY", "EUR", D, table(), max_hops=MAX_HOPS)


def test_nearest_date_fallback_is_reported() -> None:
    """A 2019 event has no quote; the nearest is used and the gap is reported."""
    asked = date(2019, 9, 3)
    result = convert_with_trace(Decimal("100"), "USD", "INR", asked, table(), max_hops=MAX_HOPS)

    assert result.amount == Decimal("8300")
    assert len(result.fallbacks) == 1
    fallback = result.fallbacks[0]
    assert fallback.requested_date == asked
    assert fallback.used_date == D
    assert fallback.gap_days == (D - asked).days
    assert "event_99" in fallback.describe("event_99")


def test_exact_date_produces_no_fallback() -> None:
    assert convert_with_trace(Decimal("1"), "USD", "INR", D, table(), max_hops=MAX_HOPS).fallbacks == ()


def test_nearest_picks_the_closer_of_two_quotes() -> None:
    near = build_rate_table(
        [
            RateQuote(
                rate_date=date(2024, 1, 15), from_currency="USD", to_currency="INR", rate=Decimal("80")
            ),
            RateQuote(
                rate_date=date(2024, 3, 15), from_currency="USD", to_currency="INR", rate=Decimal("90")
            ),
        ]
    )
    assert convert(Decimal("1"), "USD", "INR", date(2024, 3, 10), near, max_hops=MAX_HOPS) == Decimal("90")
    assert convert(Decimal("1"), "USD", "INR", date(2024, 1, 20), near, max_hops=MAX_HOPS) == Decimal("80")


def test_round_trip_through_a_singly_quoted_pair_is_exact() -> None:
    """EUR->ZAR is quoted, ZAR->EUR is its exact inverse, so x survives.

    The same is NOT true of EUR<->USD: both directions are quoted independently
    (1.10 and 0.92) and are deliberately not reciprocal, so a EUR->USD->EUR
    round trip legitimately loses value. That is the data, not a bug.
    """
    t = table()
    x = Decimal("1234.56")
    back = convert(
        convert(x, "EUR", "ZAR", D, t, max_hops=MAX_HOPS), "ZAR", "EUR", D, t, max_hops=MAX_HOPS
    )
    assert abs(back - x) < Decimal("0.0000001")


# -- against the real file ---------------------------------------------------


def test_real_table_has_exactly_the_five_documented_pairs() -> None:
    real = load_rates(default_config().paths.exchange_rates_csv)
    assert real.quoted_pairs == (
        ("EUR", "USD"),
        ("EUR", "ZAR"),
        ("USD", "EUR"),
        ("USD", "IDR"),
        ("USD", "INR"),
    )
    assert real.currencies == frozenset({"EUR", "USD", "ZAR", "IDR", "INR"})


def test_real_table_connects_every_currency_pair() -> None:
    real = load_rates(default_config().paths.exchange_rates_csv)
    cfg = default_config()
    for source in sorted(real.currencies):
        for target in sorted(real.currencies):
            amount = convert(
                Decimal("100"), source, target, date(2024, 6, 15), real, max_hops=cfg.fx.max_conversion_hops
            )
            assert amount > 0
