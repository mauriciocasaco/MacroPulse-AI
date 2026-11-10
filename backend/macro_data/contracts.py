"""Pinned M2 contracts and source-independent observations."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import re
from types import MappingProxyType


ECB_TERMS = (
    "https://www.ecb.europa.eu/stats/ecb_statistics/"
    "governance_and_quality_framework/html/usage_policy.en.html"
)
EUROSTAT_TERMS = "https://ec.europa.eu/eurostat/help/copyright-notice"


@dataclass(frozen=True)
class Indicator:
    id: str
    provider: str
    dataset: str
    dimensions: tuple[tuple[str, str], ...]
    frequency: str
    unit: str
    multiplier: int
    geography: str
    adjustment: str
    title: str
    source_url: str
    reuse_url: str
    attribution: str

    @property
    def series_key(self) -> str:
        if self.provider == "ecb":
            return self.dataset + "." + ".".join(value for _, value in self.dimensions)
        return self.dataset + "?" + "&".join(f"{name}={value}" for name, value in self.dimensions)


def _ecb(id, dataset, dimensions, frequency, unit, multiplier, geography, adjustment, title, attribution):
    series_key = dataset + "." + ".".join(value for _, value in dimensions)
    return Indicator(
        id, "ecb", dataset, dimensions, frequency, unit, multiplier, geography, adjustment, title,
        f"https://data.ecb.europa.eu/data/datasets/{dataset}/{series_key}", ECB_TERMS, attribution,
    )


CATALOG = MappingProxyType({item.id: item for item in (
    _ecb(
        "eur-usd", "EXR", (
            ("FREQ", "D"), ("CURRENCY", "USD"), ("CURRENCY_DENOM", "EUR"),
            ("EXR_TYPE", "SP00"), ("EXR_SUFFIX", "A"),
        ), "daily", "USD/EUR", 0, "bilateral", "not_applicable",
        "EUR/USD ECB reference exchange rate", "ECB statistics",
    ),
    _ecb(
        "m3-stock", "BSI", (
            ("FREQ", "M"), ("REF_AREA", "U2"), ("ADJUSTMENT", "Y"), ("BS_REP_SECTOR", "V"),
            ("BS_ITEM", "M30"), ("MATURITY_ORIG", "X"), ("DATA_TYPE", "1"), ("COUNT_AREA", "U2"),
            ("BS_COUNT_SECTOR", "2300"), ("CURRENCY_TRANS", "Z01"), ("BS_SUFFIX", "E"),
        ), "monthly", "EUR", 6, "euro_area_changing", "seasonal_and_calendar",
        "Euro area M3, end-of-month stock", "ECB/ESCB statistics",
    ),
    _ecb(
        "deposit-rate", "FM", (
            ("FREQ", "B"), ("REF_AREA", "U2"), ("CURRENCY", "EUR"), ("PROVIDER_FM", "4F"),
            ("INSTRUMENT_FM", "KR"), ("PROVIDER_FM_ID", "DFR"), ("DATA_TYPE_FM", "LEV"),
        ), "effective_date", "percent_per_annum", 0, "euro_area_changing", "not_applicable",
        "ECB deposit facility rate, effective-date changes", "European Central Bank",
    ),
    Indicator(
        "real-gdp-qoq", "eurostat", "namq_10_gdp", (
            ("freq", "Q"), ("unit", "CLV_PCH_PRE"), ("s_adj", "SCA"), ("na_item", "B1GQ"), ("geo", "EA21"),
        ), "quarterly", "percent_qoq", 0, "euro_area_21_fixed", "seasonal_and_calendar",
        "Euro area 21 real GDP, quarter-on-quarter growth", "https://doi.org/10.2908/NAMQ_10_GDP",
        EUROSTAT_TERMS, "Eurostat, namq_10_gdp",
    ),
    Indicator(
        "hicp-yoy", "eurostat", "prc_hicp_minr", (
            ("freq", "M"), ("unit", "RCH_A"), ("coicop18", "TOTAL"), ("geo", "EA"),
        ), "monthly", "percent_yoy", 0, "euro_area_changing", "unadjusted",
        "Euro area all-items HICP, year-on-year change",
        "https://ec.europa.eu/eurostat/databrowser/product/view/prc_hicp_minr",
        EUROSTAT_TERMS, "Eurostat, prc_hicp_minr",
    ),
)})


@dataclass(frozen=True)
class Period:
    raw: str
    start: date
    end: date
    effective_date: date | None = None


def parse_period(frequency: str, raw: str) -> Period:
    if frequency in {"daily", "effective_date"} and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        day = date.fromisoformat(raw)
        return Period(raw, day, day, day if frequency == "effective_date" else None)
    if frequency == "monthly" and re.fullmatch(r"\d{4}-\d{2}", raw):
        year, month = map(int, raw.split("-"))
        return Period(raw, date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))
    if frequency == "quarterly" and re.fullmatch(r"\d{4}-Q[1-4]", raw):
        year, quarter = int(raw[:4]), int(raw[-1])
        return Period(raw, date(year, quarter * 3 - 2, 1), date(year, quarter * 3, (31, 30, 30, 31)[quarter - 1]))
    raise ValueError("unexpected provider period")


def format_period(frequency: str, day: date) -> str:
    if frequency == "monthly":
        return day.isoformat()[:7]
    if frequency == "quarterly":
        return f"{day.year:04}-Q{(day.month - 1) // 3 + 1}"
    return day.isoformat()


@dataclass(frozen=True)
class Observation:
    period: Period
    value: Decimal | None
    value_raw: str | None
    status_raw: str | None
    status_kind: str
    confidentiality_raw: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    provider_valid_from: datetime | None = None
    provider_valid_to: datetime | None = None
    provider_action: str | None = None


def parse_value(raw: str) -> Decimal:
    # Explicit finite decimal syntax and bounded precision keep storage and
    # arithmetic within the documented public contracts.
    if len(raw) > 80 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d{1,3})?", raw):
        raise ValueError("unexpected provider value")
    value = Decimal(raw)
    if not value.is_finite() or abs(value.adjusted()) > 50:
        raise ValueError("provider value outside supported precision")
    return value
