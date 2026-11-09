"""Versioned GKG metadata association rules."""

from dataclasses import dataclass
import re

from backend.market_data.catalog import ASSET_SOURCES

from .gdelt import Record, url_terms


METHOD_VERSION = "gkg-asset-aliases-v1"


@dataclass(frozen=True)
class Association:
    asset_id: str
    relationship: str
    confidence: str
    evidence: str


_DIRECT = {
    "gold_future": ("gold futures", "comex gold"),
    "wti_future": ("west texas intermediate", "wti crude", "wti"),
    "sp500_index": ("s p 500", "sp 500", "standard poor s 500", "standard and poor s 500"),
    "world_equity_etf": ("vanguard total world stock etf", "vanguard total world stock"),
    "emerging_markets_etf": ("vanguard ftse emerging markets etf", "vanguard ftse emerging markets"),
    "bitcoin_usd": ("bitcoin",),
}
_THEMATIC = {
    "gold_future": ("gold price", "gold prices", "gold rally", "gold record", "golds record", "gold hits", "gold tops", "econ goldprice"),
    "wti_future": ("oil price", "oil prices", "crude oil", "econ oilprice"),
    "sp500_index": ("wall street", "us stocks", "us equities", "stock market"),
    "world_equity_etf": ("global equity", "global equities", "global stocks", "world stocks"),
    "emerging_markets_etf": ("emerging market", "emerging markets", "emerging economies"),
    "bitcoin_usd": ("crypto market", "cryptocurrency"),
}
_MACRO = ("federal reserve", "fed rate", "fed raises", "rate hike", "interest rate", "interest rates", "monetary policy")


def normalize_terms(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def associate(record: Record) -> tuple[Association, ...]:
    fields = [("URL path", url_terms(record))]
    fields += [("GKG entity", item) for item in record.entities]
    fields += [("GKG theme", item) for item in record.themes]
    fields = [(field, f" {normalize_terms(value)} ") for field, value in fields]
    matches = []
    for asset in ASSET_SOURCES:
        groups = [
            ("direct", "high", _DIRECT[asset]),
            ("thematic", "medium", _THEMATIC[asset]),
        ]
        if asset in ("world_equity_etf", "emerging_markets_etf"):
            groups.append(("macro", "medium", _MACRO))
        association = _match(asset, groups, fields)
        if association:
            matches.append(association)
    return tuple(matches)


def _match(asset, groups, fields) -> Association | None:
    for relation, confidence, aliases in groups:
        for alias in aliases:
            for field, normalized in fields:
                if relation == "direct" and field == "GKG theme":
                    continue
                if f" {alias} " in normalized:
                    return Association(asset, relation, confidence, f"{field} alias: {alias}")
    return None
