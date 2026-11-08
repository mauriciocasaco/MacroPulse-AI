"""Initial economic asset identities and their Yahoo Finance symbols."""

from __future__ import annotations

from .models import Asset, AssetSource


ASSETS: dict[str, Asset] = {
    "gold_future": Asset("gold_future", "Gold", "commodities", "future", "USD", "USD/troy_oz", "gold_futures", "strictly_positive"),
    "wti_future": Asset("wti_future", "WTI crude oil", "commodities", "future", "USD", "USD/bbl", "wti_futures", "signed_allowed"),
    "sp500_index": Asset("sp500_index", "S&P 500", "equity", "index", None, "index_points", "us_equity", "strictly_positive"),
    "world_equity_etf": Asset("world_equity_etf", "Global equity market", "equity", "etf", "USD", "USD/share", "vt_sessions", "strictly_positive"),
    "emerging_markets_etf": Asset("emerging_markets_etf", "Emerging markets", "equity", "etf", "USD", "USD/share", "vwo_sessions", "strictly_positive"),
    "bitcoin_usd": Asset("bitcoin_usd", "Bitcoin", "cryptoassets", "cryptocurrency_spot", "USD", "USD/BTC", "24_7", "strictly_positive"),
}


ASSET_SOURCES: dict[str, AssetSource] = {
    "gold_future": AssetSource("gold_future", "future", "yahoo_finance", "GC=F", rolling_method="unknown"),
    "wti_future": AssetSource("wti_future", "future", "yahoo_finance", "CL=F", rolling_method="unknown"),
    "sp500_index": AssetSource("sp500_index", "index", "yahoo_finance", "^GSPC"),
    "world_equity_etf": AssetSource("world_equity_etf", "etf", "yahoo_finance", "VT"),
    "emerging_markets_etf": AssetSource("emerging_markets_etf", "etf", "yahoo_finance", "VWO"),
    "bitcoin_usd": AssetSource(
        "bitcoin_usd", "cryptocurrency_spot", "yahoo_finance", "BTC-USD",
        source_convention="verified:yahoo_bitcoin_utc_date:v1",
    ),
}


def get_asset_source(asset_id: str) -> AssetSource:
    try:
        return ASSET_SOURCES[asset_id]
    except KeyError as exc:
        raise ValueError(f"unknown asset id: {asset_id}") from exc
