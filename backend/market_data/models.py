"""Small, provider-neutral types for raw daily price retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Mapping, Protocol


@dataclass(frozen=True)
class DateRange:
    start: date
    end_exclusive: date

    def __post_init__(self) -> None:
        if not isinstance(self.start, date) or isinstance(self.start, datetime):
            raise ValueError("start must be a date")
        if not isinstance(self.end_exclusive, date) or isinstance(self.end_exclusive, datetime):
            raise ValueError("end_exclusive must be a date")
        if self.start >= self.end_exclusive:
            raise ValueError("start must be earlier than end_exclusive")


@dataclass(frozen=True)
class AssetSource:
    asset_id: str
    instrument_type: str
    source_id: str
    symbol: str
    source_convention: str = "unknown"
    rolling_method: str | None = None


@dataclass(frozen=True)
class RawRow:
    original_label: date | datetime | int | str
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True)
class RawBatch:
    asset_id: str
    mapping: AssetSource
    provider: str
    ticker: str
    requested_range: DateRange
    interval: str
    retrieved_at: datetime
    adapter_version: str
    rows: tuple[RawRow, ...]
    temporal_metadata: Mapping[str, object]
    provider_version: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() != timezone.utc.utcoffset(self.retrieved_at):
            raise ValueError("retrieved_at must be UTC-aware")
        object.__setattr__(self, "temporal_metadata", MappingProxyType(dict(self.temporal_metadata)))


class PriceAdapter(Protocol):
    def fetch_daily(self, mapping: AssetSource, window: DateRange) -> RawBatch: ...
