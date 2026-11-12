"""Deterministic operands and calculations from the existing read-only series reader."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..macro_data.api import MacroHistory, MacroObservation, MacroWindow, indicator_metadata
from ..macro_data.contracts import CATALOG, parse_period
from .models import Claim, IndicatorId, ObservedChange
from .statistics import _NAMES, _PUBLISHABLE, _UNITS, _decimal, _level_baseline_floor, _observed_change
from .v2_models import Requirement, SeriesEvidence


@dataclass(frozen=True)
class Definition:
    geography: tuple[str, ...]
    measure: str
    variant: str
    difference_unit: str
    display_difference_unit: str


# Exact comparisons use the definition checks from M7.D.1 (unit, variant,
# geography, adjustment and population) against the pinned official catalog.
DEFINITIONS: dict[IndicatorId, Definition] = {
    "hicp-yoy": Definition(("euro_area", "euro_area_changing"), "annual_inflation", "headline",
                           "percentage_points", "percentage points"),
    "real-gdp-qoq": Definition(("euro_area", "euro_area_21_fixed"), "real_gdp_qoq", "real",
                               "percentage_points", "percentage points"),
    "eur-usd": Definition(("bilateral",), "spot_reference_rate", "spot", "USD/EUR", "USD per EUR"),
    "m3-stock": Definition(("euro_area", "euro_area_changing"), "m3_stock", "stock", "EUR", "millions of EUR"),
    "deposit-rate": Definition(("euro_area", "euro_area_changing"), "deposit_facility_rate", "nominal",
                               "percentage_points", "percentage points"),
}


def matches_definition(key: IndicatorId, requirement: Requirement) -> bool:
    definition = DEFINITIONS[key]
    item = CATALOG[key]
    unit = {
        "difference": definition.difference_unit,
        "relative_change": "percent",
    }.get(requirement.operation, item.unit)
    return (
        requirement.geography in definition.geography
        and requirement.measure == definition.measure
        and requirement.variant in {None, definition.variant}
        and requirement.adjustment in {None, item.adjustment}
        and requirement.unit in {None, unit}
        and not (requirement.population or requirement.subgroup or requirement.other_constraints)
    )


def exact_period(window: MacroWindow, frequency: str) -> bool:
    start, end = window.start, window.end
    if frequency == "daily":
        return start == end
    if frequency == "monthly":
        month = start.month
    elif frequency == "quarterly" and start.month in {1, 4, 7, 10}:
        month = start.month + 2
    else:
        return False
    return start.day == 1 and end == date(start.year, month, monthrange(start.year, month)[1])


@dataclass(frozen=True)
class SeriesResult:
    evidence: SeriesEvidence
    fact: Claim
    calculation: Claim | None
    movement: ObservedChange | None
    provisional: bool


def summarize_requirement(
    history: MacroHistory, key: IndicatorId, window: MacroWindow, requirement: Requirement,
) -> SeriesResult | None:
    item = CATALOG[key]
    if history.indicator != indicator_metadata(item):
        return None
    rows = sorted((row for row in history.observations
                   if window.start <= row.period_start <= row.period_end <= window.end),
                  key=lambda row: row.period_start)
    if any(row.status_kind == "break" for row in rows):
        return None
    rows = [row for row in rows if row.value is not None and row.value.is_finite()
            and row.status_kind in _PUBLISHABLE]
    if (len({row.period_start for row in rows}) != len(rows)
            or any(not _matches_period(row, item.frequency) for row in rows)):
        return None

    operation = requirement.operation
    if operation == "point":
        if len(rows) != 1 or (rows[0].period_start, rows[0].period_end) != (window.start, window.end):
            return None
        selected = rows
    elif operation in {"difference", "relative_change"}:
        by_period = {(row.period_start, row.period_end): row for row in rows}
        selected = [by_period.get((operand.start, operand.end)) for operand in requirement.operands]
        if None in selected:
            return None
    elif len(rows) >= 2:
        # Endpoints show the overall movement, while extrema reveal a shock
        # and rebound even when the two endpoints have the same sign.
        selected = list({row.period_start: row for row in (
            rows[0], min(rows, key=lambda row: row.value), max(rows, key=lambda row: row.value), rows[-1],
        )}.values())
        selected.sort(key=lambda row: row.period_start)
    else:
        return None

    first, last = selected[0], selected[-1]
    citation = f"series:{key}"
    observed = ", ".join(_observation_text(row, key) for row in selected)
    fact = Claim(text=f"{item.title}: {observed}.", citations=[citation])
    calculation = None
    if operation != "point":
        value = last.value - first.value
        unit = DEFINITIONS[key].display_difference_unit
        if operation == "relative_change":
            if first.value <= _level_baseline_floor(selected) or last.value < 0:
                return None
            value = (value / first.value * 100).quantize(Decimal("0.000001")).normalize()
            unit = "%"
        suffix = unit if unit == "%" else f" {unit}"
        calculation = Claim(
            text=(f"From {first.period_raw} to {last.period_raw}, the change in "
                  f"{_NAMES[key]} was {_decimal(value)}{suffix}."),
            citations=[citation],
        )
    movement = _observed_change(key, [first, last], citation) if first.period_start < last.period_start else None
    return SeriesResult(
        SeriesEvidence(id=citation, kind="series", title=item.title, url=item.source_url,
                       attribution=item.attribution, period_start=first.period_start,
                       period_end=last.period_end, updated_at=history.updated_at, rights_url=item.reuse_url,
                       series_key=item.series_key, unit=item.unit, multiplier=item.multiplier,
                       geography=item.geography, adjustment=item.adjustment),
        fact, calculation, movement, any(row.status_kind != "normal" for row in selected),
    )


def _observation_text(row: MacroObservation, key: IndicatorId) -> str:
    unit = _UNITS[key]
    suffix = unit if unit.startswith("%") else f" {unit}"
    return f"{_decimal(row.value)}{suffix} in {row.period_raw}"


def _matches_period(row: MacroObservation, frequency: str) -> bool:
    try:
        period = parse_period(frequency, row.period_raw)
    except ValueError:
        return False
    return (period.start, period.end, period.effective_date) == (row.period_start, row.period_end, row.effective_date)
