"""Currency conversion from `dataset/exchange_rates.csv`.

Answers "what is X <from> worth in <to> on <date>" in Decimal, never float.

The table quotes only five DIRECTED pairs -- EUR>USD, EUR>ZAR, USD>EUR,
USD>IDR, USD>INR -- so an inverse must be derived as 1/rate and some
conversions need a pivot (ZAR->INR = ZAR->EUR->USD->INR). Pathfinding is a
breadth-first search over the currency graph, bounded by
`FxConfig.max_conversion_hops`.

Rates are quoted monthly, on the 15th, spanning 2023-10 .. 2026-11. Events
outside that span (one labelled row is dated 2019) fall back to the nearest
available `rate_date`; every fallback is reported in `ConversionResult.fallbacks`
so the caller can log it against an event_id.

`load_rates` is the only I/O. `convert` is pure.
"""

from __future__ import annotations

import csv
from collections import deque
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from src.contract import to_money

#: A 3-letter ISO currency code as it appears in the dataset.
Currency = str


class FXError(ValueError):
    """No usable path exists between two currencies, or the table is empty."""


class RateQuote(BaseModel):
    """One row of `exchange_rates.csv`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rate_date: date
    from_currency: Currency
    to_currency: Currency
    rate: Decimal


class RateHop(BaseModel):
    """One leg of a conversion, with the quote that was actually used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_currency: Currency
    to_currency: Currency
    rate: Decimal
    #: The date the caller asked for.
    requested_date: date
    #: The `rate_date` actually used -- may differ when the table has no quote.
    used_date: date
    #: True when the quote was derived as 1/rate from the reverse direction.
    inverted: bool

    @property
    def gap_days(self) -> int:
        return abs((self.used_date - self.requested_date).days)


class FXFallback(BaseModel):
    """A hop that could not be priced on the requested date.

    Carries everything needed for a log line: which pair, which date was asked
    for, which was used, and how far apart they are.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_currency: Currency
    to_currency: Currency
    requested_date: date
    used_date: date
    gap_days: int

    def describe(self, event_id: str | None = None) -> str:
        subject = f"event {event_id}: " if event_id else ""
        return (
            f"{subject}no {self.from_currency}->{self.to_currency} quote on "
            f"{self.requested_date}; used {self.used_date} ({self.gap_days}d away)"
        )


class ConversionResult(BaseModel):
    """The converted amount plus the full provenance of how it got there."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal
    from_currency: Currency
    to_currency: Currency
    on_date: date
    hops: tuple[RateHop, ...] = ()
    fallbacks: tuple[FXFallback, ...] = ()


class RateTable(BaseModel):
    """Quotes indexed by directed pair, each list sorted by `rate_date`.

    `neighbours` is the UNDIRECTED adjacency used for pathfinding: a quoted
    A->B edge makes B->A reachable by inversion.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    quotes: Mapping[tuple[Currency, Currency], tuple[RateQuote, ...]] = Field(default_factory=dict)
    neighbours: Mapping[Currency, tuple[Currency, ...]] = Field(default_factory=dict)

    @property
    def currencies(self) -> frozenset[Currency]:
        return frozenset(self.neighbours)

    @property
    def quoted_pairs(self) -> tuple[tuple[Currency, Currency], ...]:
        return tuple(sorted(self.quotes))


def build_rate_table(quotes: Iterable[RateQuote]) -> RateTable:
    """Index quotes by directed pair and derive the undirected adjacency. Pure."""
    indexed: dict[tuple[Currency, Currency], list[RateQuote]] = {}
    adjacency: dict[Currency, set[Currency]] = {}

    for quote in quotes:
        indexed.setdefault((quote.from_currency, quote.to_currency), []).append(quote)
        adjacency.setdefault(quote.from_currency, set()).add(quote.to_currency)
        adjacency.setdefault(quote.to_currency, set()).add(quote.from_currency)

    return RateTable(
        quotes={
            pair: tuple(sorted(rows, key=lambda q: q.rate_date)) for pair, rows in indexed.items()
        },
        # Sorted so breadth-first search explores deterministically.
        neighbours={ccy: tuple(sorted(peers)) for ccy, peers in adjacency.items()},
    )


def load_rates(path: str | Path) -> RateTable:
    """Read `exchange_rates.csv` into a `RateTable`. The only I/O here."""
    rows: list[RateQuote] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                RateQuote(
                    rate_date=date.fromisoformat(raw["rate_date"].strip()),
                    from_currency=raw["from_currency"].strip().upper(),
                    to_currency=raw["to_currency"].strip().upper(),
                    rate=to_money(raw["rate"]),
                )
            )
    if not rows:
        raise FXError(f"no exchange rates found in {path}")
    return build_rate_table(rows)


def find_path(
    table: RateTable,
    from_currency: Currency,
    to_currency: Currency,
    *,
    max_hops: int,
) -> tuple[Currency, ...]:
    """Fewest-hop currency chain from `from_currency` to `to_currency`.

    Breadth-first over sorted neighbours, so the result is deterministic when
    two chains are the same length. Raises `FXError` when nothing links them
    within `max_hops`.
    """
    if from_currency == to_currency:
        return (from_currency,)
    if from_currency not in table.neighbours:
        raise FXError(f"currency {from_currency} does not appear in the rate table")
    if to_currency not in table.neighbours:
        raise FXError(f"currency {to_currency} does not appear in the rate table")

    queue: deque[tuple[Currency, ...]] = deque([(from_currency,)])
    seen: set[Currency] = {from_currency}
    while queue:
        chain = queue.popleft()
        if len(chain) - 1 >= max_hops:
            continue
        for neighbour in table.neighbours.get(chain[-1], ()):
            if neighbour in seen:
                continue
            extended = (*chain, neighbour)
            if neighbour == to_currency:
                return extended
            seen.add(neighbour)
            queue.append(extended)

    raise FXError(
        f"no conversion path from {from_currency} to {to_currency} within {max_hops} hops"
    )


def _nearest(quotes: tuple[RateQuote, ...], on_date: date) -> RateQuote:
    """The quote whose `rate_date` is closest to `on_date`.

    Ties break to the EARLIER quote: deterministic, and it avoids pricing an
    event with a rate published after it.
    """
    return min(quotes, key=lambda q: (abs((q.rate_date - on_date).days), q.rate_date))


def resolve_hop(
    table: RateTable,
    from_currency: Currency,
    to_currency: Currency,
    on_date: date,
) -> RateHop:
    """Price one leg, preferring a direct quote and inverting only if needed."""
    direct = table.quotes.get((from_currency, to_currency))
    if direct:
        quote = _nearest(direct, on_date)
        return RateHop(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=quote.rate,
            requested_date=on_date,
            used_date=quote.rate_date,
            inverted=False,
        )

    reverse = table.quotes.get((to_currency, from_currency))
    if reverse:
        quote = _nearest(reverse, on_date)
        if quote.rate == 0:
            raise FXError(f"cannot invert a zero {to_currency}->{from_currency} rate")
        return RateHop(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=Decimal(1) / quote.rate,
            requested_date=on_date,
            used_date=quote.rate_date,
            inverted=True,
        )

    raise FXError(f"no quote in either direction for {from_currency}<->{to_currency}")


def convert_with_trace(
    amount: Decimal | float | int | str,
    from_currency: Currency,
    to_currency: Currency,
    on_date: date,
    table: RateTable,
    *,
    max_hops: int,
) -> ConversionResult:
    """Convert and report every hop and every nearest-date fallback. Pure."""
    value = to_money(amount)
    source = from_currency.strip().upper()
    target = to_currency.strip().upper()

    if source == target:
        # Identity never touches the table -- a user whose events are all in
        # their home currency must work even if the table lacks that currency.
        return ConversionResult(
            amount=value, from_currency=source, to_currency=target, on_date=on_date
        )

    chain = find_path(table, source, target, max_hops=max_hops)

    hops: list[RateHop] = []
    fallbacks: list[FXFallback] = []
    running = value
    for leg_from, leg_to in zip(chain, chain[1:]):
        hop = resolve_hop(table, leg_from, leg_to, on_date)
        hops.append(hop)
        running = running * hop.rate
        if hop.used_date != on_date:
            fallbacks.append(
                FXFallback(
                    from_currency=leg_from,
                    to_currency=leg_to,
                    requested_date=on_date,
                    used_date=hop.used_date,
                    gap_days=hop.gap_days,
                )
            )

    return ConversionResult(
        amount=running,
        from_currency=source,
        to_currency=target,
        on_date=on_date,
        hops=tuple(hops),
        fallbacks=tuple(fallbacks),
    )


def convert(
    amount: Decimal | float | int | str,
    from_currency: Currency,
    to_currency: Currency,
    on_date: date,
    table: RateTable,
    *,
    max_hops: int,
) -> Decimal:
    """Convert `amount` into `to_currency`. See `convert_with_trace` for provenance."""
    return convert_with_trace(
        amount, from_currency, to_currency, on_date, table, max_hops=max_hops
    ).amount
