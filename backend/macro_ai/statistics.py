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
    "eur-usd": "USD por EUR",
    "m3-stock": "millones de EUR",
    "deposit-rate": "% anual",
    "real-gdp-qoq": "% intertrimestral",
    "hicp-yoy": "% interanual",
}
_NAMES = {
    "eur-usd": "EUR/USD",
    "m3-stock": "saldo de M3",
    "deposit-rate": "tasa de facilidad de depósito",
    "real-gdp-qoq": "crecimiento del PIB real",
    "hicp-yoy": "inflación general IAPC",
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
        "Se usan las versiones capturadas disponibles localmente. El corte solicitado se refiere a los períodos observados.",
        "La coincidencia temporal y las correlaciones descriptivas no identifican causalidad.",
    ]
    aligned: dict[str, dict[str, Decimal]] = {}
    complete = True
    for key in indicator_ids:
        history = histories[key]
        item = CATALOG[key]
        rows = [row for row in history.observations
                if row.value is not None and row.status_kind in _PUBLISHABLE
                and request.start <= row.period_start and row.period_end <= request.end]
        anchor = history.anchor if history.coverage_start == request.start else None
        if (anchor is not None and anchor.value is not None and anchor.status_kind in _PUBLISHABLE
                and (not rows or rows[0].period_start > request.start)):
            rows.insert(0, anchor)
        if len(rows) < 2:
            complete = False
            continue
        if any(row.status_kind in {"estimated", "provisional"} for row in rows):
            limitations.append(f"La serie de {_NAMES[key]} contiene observaciones estimadas o provisionales.")
        if item.frequency == "effective_date" and history.coverage_end is not None:
            limitations.append(f"La primera cobertura continua verificada de la tasa termina el {history.coverage_end}.")
        citation = f"series:{key}"
        first, last = rows[0], rows[-1]
        evidence.append(Evidence(
            id=citation, kind="series", title=item.title, url=item.source_url, attribution=item.attribution,
            period_start=first.period_start, period_end=last.period_end,
        ))
        minimum, maximum = min(rows, key=lambda row: row.value), max(rows, key=lambda row: row.value)
        context = " La primera fecha es el evento efectivo previo usado como ancla." if rows[0] is anchor else ""
        facts.append(Claim(
            text=(f"{_NAMES[key]}: {_decimal(first.value)} {_UNITS[key]} en {first.period_raw} y "
                  f"{_decimal(last.value)} {_UNITS[key]} en {last.period_raw}. "
                  f"Extremos de la muestra: {_decimal(minimum.value)} en {minimum.period_raw} y "
                  f"{_decimal(maximum.value)} en {maximum.period_raw}.{context}"),
            citations=[citation],
        ))
        delta = last.value - first.value
        if key == "deposit-rate":
            magnitude, unit = delta * 100, "puntos básicos"
        elif key in {"hicp-yoy", "real-gdp-qoq"}:
            magnitude, unit = delta, "puntos porcentuales"
        else:
            magnitude, unit = delta, _UNITS[key]
        calculations.append(Claim(
            text=(f"Diferencia entre {first.period_raw} y {last.period_raw} para "
                  f"{_NAMES[key]}: {_decimal(magnitude)} {unit}."),
            citations=[citation],
        ))
        if key in {"m3-stock", "hicp-yoy"}:
            aligned[key] = {row.period_raw: row.value for row in rows}

    if {"m3-stock", "hicp-yoy"}.issubset(indicator_ids) and complete:
        limitations.append(
            "La variación interanual se calcula a partir de saldos de M3 y difiere de la tasa oficial ajustada del BCE."
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
            limitations.append("La correlación necesita al menos tres pares mensuales interanuales con variación.")
        else:
            calculations.append(Claim(
                text=(f"Correlación de Pearson entre crecimiento interanual del saldo de M3 e IAPC interanual: "
                      f"{correlation:.2f} con {len(pairs)} pares coincidentes de {pair_periods[0]} a {pair_periods[-1]}."),
                citations=["series:m3-stock", "series:hicp-yoy"],
            ))
    return Summary(evidence, facts, calculations, limitations, complete)
