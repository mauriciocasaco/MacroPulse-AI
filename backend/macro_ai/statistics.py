"""Deterministic, source-attributed statistics for research answers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from ..macro_data.api import MacroHistory, MacroWindow, load_history, open_macro_reader
from ..macro_data.contracts import CATALOG
from .models import Claim, Evidence, ResearchRequest


_UNITS = {
    "eur-usd": "USD per EUR", "m3-stock": "millions of EUR", "deposit-rate": "% per year",
    "real-gdp-qoq": "% quarter on quarter", "hicp-yoy": "% year on year",
}
_NAMES = {
    "eur-usd": "EUR/USD", "m3-stock": "M3 stock", "deposit-rate": "deposit facility rate",
    "real-gdp-qoq": "real GDP growth", "hicp-yoy": "headline HICP inflation",
}
_PUBLISHABLE = frozenset({"normal", "estimated", "provisional", "break"})


class Summary(NamedTuple):
    evidence: list[Evidence]
    facts: list[Claim]
    calculations: list[Claim]
    limitations: list[str]
    complete: bool


def read_statistics(indicator_ids: tuple[str, ...], request: ResearchRequest) -> dict[str, MacroHistory]:
    window = MacroWindow(start=request.start, end=request.end)
    with open_macro_reader() as connection:
        return {key: load_history(connection, CATALOG[key], window) for key in indicator_ids}


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _month_distance(period: str) -> int:
    year, month = map(int, period.split("-"))
    return year * 12 + month - 1


def _pearson(pairs: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    if len(pairs) < 3:
        return None
    mean_x = sum(x for x, _ in pairs) / len(pairs)
    mean_y = sum(y for _, y in pairs) / len(pairs)
    centered = [(x - mean_x, y - mean_y) for x, y in pairs]
    denominator = (sum(x * x for x, _ in centered) * sum(y * y for _, y in centered)).sqrt()
    return sum(x * y for x, y in centered) / denominator if denominator else None


def summarize(
    histories: Mapping[str, MacroHistory], indicator_ids: tuple[str, ...], request: ResearchRequest,
) -> Summary:
    evidence: list[Evidence] = []
    facts: list[Claim] = []
    calculations: list[Claim] = []
    limitations = [
        "Locally captured data vintages are used. The cutoff concerns observation periods, not historical publication vintages.",
        "Temporal overlap and descriptive correlations do not establish causality.",
    ]
    aligned: dict[str, dict[str, Decimal]] = {}
    complete = True
    for key in indicator_ids:
        history = histories[key]
        item = CATALOG[key]
        rows = [row for row in history.observations
                if row.value is not None and row.status_kind in _PUBLISHABLE
                and request.start <= row.period_start and row.period_end <= request.end]
        rows.sort(key=lambda row: (row.period_start, row.period_end))
        anchor = history.anchor if not request.event_id and history.coverage_start == request.start else None
        if (anchor is not None and anchor.value is not None and anchor.status_kind in _PUBLISHABLE
                and (not rows or rows[0].period_start > request.start)):
            rows.insert(0, anchor)
        if any(row.status_kind == "break" for row in rows):
            limitations.append(f"The series for {_NAMES[key]} has a statistical break; comparable endpoint and swing calculations are unavailable.")
            complete = False
            continue
        if len(rows) < 2:
            complete = False
            continue
        if any(row.status_kind in {"estimated", "provisional"} for row in rows):
            limitations.append(f"The series for {_NAMES[key]} contains estimated or provisional observations.")
        if item.frequency == "effective_date" and history.coverage_end is not None:
            limitations.append(f"The first verified continuous rate coverage ends on {history.coverage_end}.")
        citation = f"series:{key}"
        first, last = rows[0], rows[-1]
        evidence.append(Evidence(
            id=citation, kind="series", title=item.title, url=item.source_url, attribution=item.attribution,
            period_start=first.period_start, period_end=last.period_end,
        ))
        minimum, maximum = min(rows, key=lambda row: row.value), max(rows, key=lambda row: row.value)
        context = " The first date is a prior effective event used as an explicitly verified anchor." if rows[0] is anchor else ""
        facts.append(Claim(
            text=(f"{_NAMES[key]}: {_decimal(first.value)} {_UNITS[key]} on {first.period_raw} and "
                  f"{_decimal(last.value)} {_UNITS[key]} on {last.period_raw}. "
                  f"Endpoint observation periods: {first.period_start} to {first.period_end}, and "
                  f"{last.period_start} to {last.period_end}. "
                  f"Sample extrema: {_decimal(minimum.value)} on {minimum.period_raw} and "
                  f"{_decimal(maximum.value)} on {maximum.period_raw}.{context}"),
            citations=[citation],
        ))
        delta = last.value - first.value
        if key == "deposit-rate":
            magnitude, unit = delta * 100, "basis points"
        elif key in {"hicp-yoy", "real-gdp-qoq"}:
            magnitude, unit = delta, "percentage points"
        else:
            magnitude, unit = delta, _UNITS[key]
        calculations.append(Claim(
            text=(f"Beginning-to-end difference between {first.period_raw} and {last.period_raw} for "
                  f"{_NAMES[key]}: {_decimal(magnitude)} {unit}."),
            citations=[citation],
        ))
        # Maximum absolute chronologically ordered excursion. The global range
        # has that magnitude; order the extrema by observation date, never claim
        # a minimum preceded a maximum when the actual series says otherwise.
        swing_first, swing_last = sorted((minimum, maximum), key=lambda row: row.period_start)
        swing_delta = swing_last.value - swing_first.value
        swing_magnitude = swing_delta * 100 if key == "deposit-rate" else swing_delta
        calculations.append(Claim(
            text=(f"Largest observed swing for {_NAMES[key]}: {_decimal(swing_first.value)} {_UNITS[key]} "
                  f"on {swing_first.period_raw} to {_decimal(swing_last.value)} {_UNITS[key]} on "
                  f"{swing_last.period_raw}; change {_decimal(swing_magnitude)} {unit}. "
                  f"Swing observation periods: {swing_first.period_start} to {swing_first.period_end}, "
                  f"and {swing_last.period_start} to {swing_last.period_end}."),
            citations=[citation],
        ))
        for label, left, right in (("Beginning-to-end", first, last), ("Largest observed swing", swing_first, swing_last)):
            # Percent changes of rate levels and signed growth rates are easily
            # confused with their economically meaningful percentage-point move.
            # For positive level series, reject baselines below 1 ppm of sample
            # scale (and an absolute 1e-12 floor), or a negative endpoint.
            scale = max(abs(row.value) for row in rows)
            floor = max(Decimal("1e-12"), scale * Decimal("1e-6"))
            if key in {"eur-usd", "m3-stock"} and left.value > floor and right.value >= 0:
                change = (right.value - left.value) / left.value * 100
                calculations.append(Claim(
                    text=(f"{label} percentage change for {_NAMES[key]} from {left.period_raw} to "
                          f"{right.period_raw}: {change:.6f}%; calculated as "
                          f"({_decimal(right.value)} - {_decimal(left.value)}) / {_decimal(left.value)} x 100."),
                    citations=[citation],
                ))
            else:
                limitations.append(f"{label} relative percentage for {_NAMES[key]} is not reported: "
                                   "the series is a rate or growth rate, or its baseline is nonpositive or near zero; "
                                   "the absolute change retains the original units.")
        if key in {"m3-stock", "hicp-yoy"}:
            aligned[key] = {row.period_raw: row.value for row in rows}

    if {"m3-stock", "hicp-yoy"}.issubset(indicator_ids) and complete:
        limitations.append(
            "Year-on-year change is calculated from M3 stocks and differs from the official adjusted ECB rate."
        )
        m3, hicp = aligned["m3-stock"], aligned["hicp-yoy"]
        m3_by_month = {_month_distance(period): value for period, value in m3.items()}
        pairs = []
        pair_periods = []
        for period in sorted(m3.keys() & hicp.keys()):
            previous = m3_by_month.get(_month_distance(period) - 12)
            if previous:
                pairs.append(((m3[period] / previous - 1) * 100, hicp[period]))
                pair_periods.append(period)
        correlation = _pearson(pairs)
        if correlation is None:
            limitations.append("Correlation requires at least three varying year-on-year monthly pairs.")
        else:
            calculations.append(Claim(
                text=(f"Pearson correlation between year-on-year M3 stock growth and annual HICP inflation: "
                      f"{correlation:.2f} with {len(pairs)} matching pairs from {pair_periods[0]} to {pair_periods[-1]}."),
                citations=["series:m3-stock", "series:hicp-yoy"],
            ))
    return Summary(evidence, facts, calculations, limitations, complete)
