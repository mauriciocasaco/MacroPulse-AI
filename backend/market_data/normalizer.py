"""Normalize raw daily provider rows into validated domain observations."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import ASSETS
from .models import (
    Asset,
    AssetSource,
    DateRange,
    NormalizedBatch,
    PriceBar1d,
    RawBatch,
    RejectedObservation,
)


class NormalizationStructureError(ValueError):
    """The batch contract is inconsistent, so row-level processing is unsafe."""


_VERIFIED_CONVENTION = re.compile(r"^verified:yahoo_exchange_date:v1:([A-Za-z0-9_+./-]+)$")
_VERIFIED_BITCOIN_CONVENTION = "verified:yahoo_bitcoin_utc_date:v1"
_PRICE_FIELDS = ("Open", "High", "Low", "Close", "Adj Close")


def normalize(
    batch: RawBatch,
    asset: Asset,
    mapping: AssetSource,
    window: DateRange,
) -> NormalizedBatch:
    """Convert a RawBatch using only an explicitly verified date convention.

    BTC-USD has a narrowly reviewed UTC date-label convention enforced against
    midnight timestamps. Other symbols require a reviewed mapping convention
    that matches the batch's provider metadata.
    """
    if batch.asset_id != asset.id or mapping.asset_id != asset.id or batch.mapping != mapping:
        raise NormalizationStructureError("batch, asset, and mapping identities do not agree")
    if batch.ticker != mapping.symbol or batch.provider != mapping.source_id:
        raise NormalizationStructureError("batch provider identity does not agree with mapping")
    if batch.interval != "1d" or batch.requested_range != window:
        raise NormalizationStructureError("batch interval or requested range does not match normalization request")

    warnings = list(batch.warnings)
    convention = mapping.source_convention
    match = _VERIFIED_CONVENTION.fullmatch(convention)
    zone: ZoneInfo | None = None
    bitcoin_verified = (
        asset.id == "bitcoin_usd"
        and mapping.source_id == "yahoo_finance"
        and mapping.symbol == "BTC-USD"
        and convention == _VERIFIED_BITCOIN_CONVENTION
        and batch.temporal_metadata.get("index_timezone") == "UTC"
        and batch.provider == "yahoo_finance"
    )
    if bitcoin_verified:
        warnings = [
            warning for warning in warnings
            if not warning.startswith("daily session label and cutoff convention are unverified")
        ]
        warnings.append(
            "Yahoo's raw adapter warning concerns the daily cutoff; the BTC-USD cross-check verifies UTC date labels only"
        )
        zone = ZoneInfo("UTC")
    elif not match or batch.temporal_metadata.get("provider_session_label_convention") != convention:
        warnings.append("daily session label convention is unverified; rows were withheld from persistence")
    else:
        try:
            zone = ZoneInfo(match.group(1))
        except ZoneInfoNotFoundError as exc:
            raise NormalizationStructureError("verified source convention uses an unknown IANA timezone") from exc
        reported_zone = batch.temporal_metadata.get("index_timezone")
        if reported_zone != match.group(1):
            raise NormalizationStructureError("verified timezone does not match provider metadata")

    bars: list[PriceBar1d] = []
    rejected: list[RejectedObservation] = []
    unknown_session_status = False
    for position, row in enumerate(batch.rows):
        if zone is None:
            rejected.append(RejectedObservation(position, "temporal_convention_unverified"))
            continue
        bar_date = _session_date(row.original_label, zone)
        if bar_date is None:
            rejected.append(RejectedObservation(position, "invalid_timestamp"))
            continue
        if bitcoin_verified and not _is_utc_midnight(row.original_label):
            rejected.append(RejectedObservation(position, "unverified_bitcoin_label"))
            continue
        if not window.start <= bar_date < window.end_exclusive:
            rejected.append(RejectedObservation(position, "outside_requested_range"))
            continue
        if row.fields.get("Close") is None:
            rejected.append(RejectedObservation(position, "missing_close"))
            continue

        values: dict[str, Decimal | None] = {}
        row_code: str | None = None
        for field in _PRICE_FIELDS:
            value, code = _decimal_value(row.fields.get(field), allow_none=True)
            values[field] = value
            if row_code is None and code is not None:
                row_code = f"invalid_{field.casefold().replace(' ', '_')}"
        volume, volume_code = _decimal_value(row.fields.get("Volume"), allow_none=True)
        if row_code is None and volume_code is not None:
            row_code = "invalid_volume"
        if row_code is not None:
            rejected.append(RejectedObservation(position, row_code))
            continue

        close = values["Close"]
        if close is None:
            rejected.append(RejectedObservation(position, "missing_close"))
            continue
        if volume is not None and volume < 0:
            rejected.append(RejectedObservation(position, "negative_volume"))
            continue

        prices = tuple(value for value in values.values() if value is not None)
        if asset.sign_policy == "strictly_positive" and any(value <= 0 for value in prices):
            rejected.append(RejectedObservation(position, "non_positive_price"))
            continue

        open_price, high, low = values["Open"], values["High"], values["Low"]
        if high is not None and low is not None and high < low:
            rejected.append(RejectedObservation(position, "inconsistent_high_low"))
            continue
        if high is not None and any(value is not None and high < value for value in (open_price, close)):
            rejected.append(RejectedObservation(position, "inconsistent_high"))
            continue
        if low is not None and any(value is not None and low > value for value in (open_price, close)):
            rejected.append(RejectedObservation(position, "inconsistent_low"))
            continue

        session_status = row.fields.get("session_status", batch.temporal_metadata.get("session_status"))
        if session_status not in ("closed", "provisional", "unknown"):
            session_status = "unknown"
        if session_status == "unknown":
            unknown_session_status = True

        bars.append(PriceBar1d(
            asset_source_id=None,
            asset_id=asset.id,
            source_id=mapping.source_id,
            symbol=mapping.symbol,
            bar_date=bar_date,
            interval="1d",
            open=open_price,
            high=high,
            low=low,
            close=close,
            adjusted_close=values["Adj Close"],
            volume=volume,
            ohlc_basis="provider_unadjusted",
            adjusted_close_basis="provider_reported" if values["Adj Close"] is not None else None,
            retrieved_at=batch.retrieved_at.astimezone(timezone.utc),
            session_status=session_status,
            quality_status="complete" if all(values[name] is not None for name in _PRICE_FIELDS[:4]) else "partial",
            source_convention=convention,
            provider=batch.provider,
            adapter_version=batch.adapter_version,
            provider_version=batch.provider_version,
            volume_unit=mapping.volume_unit,
        ))

    if unknown_session_status:
        warnings.append("session status could not be determined for one or more bars")

    provenance = {
        "asset_id": asset.id,
        "source_id": mapping.source_id,
        "symbol": mapping.symbol,
        "provider": batch.provider,
        "adapter_version": batch.adapter_version,
        "provider_version": batch.provider_version,
        "retrieved_at": batch.retrieved_at,
        "requested_start": window.start,
        "requested_end_exclusive": window.end_exclusive,
        "source_convention": convention,
        "temporal_convention_status": "verified_for_utc_date_labels" if bitcoin_verified else (
            "verified" if match and zone is not None else "unverified"
        ),
        "rolling_method": mapping.rolling_method,
        "volume_unit": mapping.volume_unit,
        "adapter_warnings": batch.warnings,
    }
    return NormalizedBatch(tuple(bars), tuple(rejected), tuple(dict.fromkeys(warnings)), provenance, len(batch.rows))


def _session_date(label: date | datetime | int | str, zone: ZoneInfo) -> date | None:
    if isinstance(label, bool):
        return None
    if isinstance(label, int):
        try:
            return datetime.fromtimestamp(label, timezone.utc).astimezone(zone).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(label, datetime):
        if label.tzinfo is None or label.utcoffset() is None:
            return None
        return label.astimezone(zone).date()
    if isinstance(label, date):
        return label
    if isinstance(label, str):
        try:
            return date.fromisoformat(label)
        except ValueError:
            return None
    return None


def _is_utc_midnight(label: date | datetime | int | str) -> bool:
    if type(label) is int:
        try:
            return datetime.fromtimestamp(label, timezone.utc).time() == time.min
        except (OverflowError, OSError, ValueError):
            return False
    return False


def _decimal_value(value: object, *, allow_none: bool) -> tuple[Decimal | None, str | None]:
    if value is None and allow_none:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None, "invalid"
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, "invalid"
    if not result.is_finite():
        return None, "invalid"
    return result, None
