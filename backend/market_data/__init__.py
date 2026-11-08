"""Market data provider contracts and adapters."""

from .catalog import ASSET_SOURCES, ASSETS, get_asset_source
from .models import Asset, AssetSource, DateRange, NormalizedBatch, PriceBar1d, RawBatch, RawRow, RejectedObservation, RunSummary
from .normalizer import NormalizationStructureError, normalize
from .pipeline import ingest_daily
from .repository import PostgresPriceRepository, PriceRepository
from .yahoo_adapter import YahooAdapter

__all__ = [
    "ASSETS", "ASSET_SOURCES", "Asset", "AssetSource", "DateRange", "NormalizedBatch",
    "NormalizationStructureError", "PostgresPriceRepository", "PriceBar1d", "PriceRepository",
    "RawBatch", "RawRow", "RejectedObservation", "RunSummary", "YahooAdapter",
    "get_asset_source", "ingest_daily", "normalize",
]
