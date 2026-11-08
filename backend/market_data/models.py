"""Small, provider-neutral types for raw daily price retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
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
    volume_unit: str | None = None


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    category: str
    instrument_type: str
    quote_currency: str | None
    economic_unit: str
    calendar_code: str
    sign_policy: Literal["strictly_positive", "signed_allowed"]


@dataclass(frozen=True)
class PriceBar1d:
    """A daily provider observation with exchange timing when independently known."""

    asset_source_id: int | None
    asset_id: str
    source_id: str
    symbol: str
    bar_date: date  # Daily date attributed by the source under source_convention.
    interval: Literal["1d"]
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    adjusted_close: Decimal | None
    volume: Decimal | None
    ohlc_basis: Literal["provider_unadjusted"]
    adjusted_close_basis: Literal["provider_reported"] | None
    retrieved_at: datetime
    session_status: Literal["closed", "provisional", "unknown"]
    quality_status: Literal["complete", "partial"]
    source_convention: str
    provider: str
    adapter_version: str
    provider_version: str | None
    volume_unit: str | None
    exchange_trade_date: date | None = None
    settlement_time: datetime | None = None
    availability_time: datetime | None = None
    rolling_method: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bar_date, date) or isinstance(self.bar_date, datetime):
            raise ValueError("bar_date must be a date, not a datetime")
        if self.interval != "1d":
            raise ValueError("PriceBar1d interval must be '1d'")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() != timezone.utc.utcoffset(self.retrieved_at):
            raise ValueError("retrieved_at must be UTC-aware")
        for field_name in ("open", "high", "low", "close", "adjusted_close", "volume"):
            value = getattr(self, field_name)
            if value is None and field_name != "close":
                continue
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal" + (" or None" if field_name != "close" else ""))
        if self.adjusted_close is None and self.adjusted_close_basis is not None:
            raise ValueError("adjusted_close_basis must be NULL when adjusted_close is NULL")
        if self.adjusted_close is not None and self.adjusted_close_basis != "provider_reported":
            raise ValueError("adjusted_close_basis must identify the provider-reported value")
        if self.session_status not in ("closed", "provisional", "unknown"):
            raise ValueError("unsupported session_status")
        if self.quality_status not in ("complete", "partial"):
            raise ValueError("unsupported quality_status")


@dataclass(frozen=True)
class RejectedObservation:
    position: int
    code: str


@dataclass(frozen=True)
class NormalizedBatch:
    bars: tuple[PriceBar1d, ...]
    rejected: tuple[RejectedObservation, ...]
    warnings: tuple[str, ...]
    provenance: Mapping[str, object]
    received_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    status: Literal["running", "succeeded", "partial", "failed", "abandoned"]
    received_count: int
    inserted_count: int
    unchanged_count: int
    corrected_count: int
    stale_count: int
    rejected_count: int
    error_code: str | None = None
    diagnostics: tuple[str, ...] = ()


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
