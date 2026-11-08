"""Initial economic asset identities and their Yahoo Finance symbols."""

from __future__ import annotations

from .models import AssetSource


ASSET_SOURCES: dict[str, AssetSource] = {
    "gold_future": AssetSource("gold_future", "future", "yahoo_finance", "GC=F", rolling_method="unknown"),
    "wti_future": AssetSource("wti_future", "future", "yahoo_finance", "CL=F", rolling_method="unknown"),
    "sp500_index": AssetSource("sp500_index", "index", "yahoo_finance", "^GSPC"),
    "world_equity_etf": AssetSource("world_equity_etf", "etf", "yahoo_finance", "VT"),
    "emerging_markets_etf": AssetSource("emerging_markets_etf", "etf", "yahoo_finance", "VWO"),
    "bitcoin_usd": AssetSource("bitcoin_usd", "cryptocurrency_spot", "yahoo_finance", "BTC-USD"),
}


def get_asset_source(asset_id: str) -> AssetSource:
    try:
        return ASSET_SOURCES[asset_id]
    except KeyError as exc:
        raise ValueError(f"unknown asset id: {asset_id}") from exc
