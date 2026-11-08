from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data.repository import PostgresPriceRepository, _Counts


WINDOW = DateRange(date(2026, 10, 5), date(2026, 10, 6))
RETRIEVED_AT = datetime(2026, 10, 7, tzinfo=timezone.utc)
MAPPING_ID = 1


@pytest.fixture
def repository():
    return PostgresPriceRepository("")


def _batch(asset_id="wti_future"):
    mapping = ASSET_SOURCES[asset_id]
    is_bitcoin = asset_id == "bitcoin_usd"
    hour, minute = (0, 0) if is_bitcoin else (13, 30)
    label = datetime(2026, 10, 5, hour, minute, tzinfo=timezone.utc)
    prices = (-5, -2, -8, -3, -4) if asset_id == "wti_future" else (5, 8, 2, 7, 6)
    fields = dict(zip(("Open", "High", "Low", "Close", "Adj Close"), prices, strict=True))
    raw = RawBatch(
        asset_id, mapping, mapping.source_id, mapping.symbol, WINDOW, "1d",
        RETRIEVED_AT, "fixture-v1", (RawRow(int(label.timestamp()), {**fields, "Volume": 0}),),
        {"index_timezone": "UTC" if is_bitcoin else "America/New_York"},
    )
    return normalize(raw, ASSETS[asset_id], mapping, WINDOW)


def _mapping_tuple(asset_id):
    mapping = ASSET_SOURCES[asset_id]
    return mapping.asset_id, mapping.source_id, mapping.symbol, mapping.source_convention


@pytest.mark.parametrize("asset_id", tuple(ASSET_SOURCES))
def test_admits_each_reviewed_yahoo_mapping(repository, asset_id):
    batch = _batch(asset_id)
    accepted = repository._validate_batch(
        batch, MAPPING_ID, _mapping_tuple(asset_id), WINDOW.start, WINDOW.end_exclusive, _Counts(),
    )
    assert len(accepted) == 1
    assert accepted[0].bar_date == WINDOW.start
    assert accepted[0].volume == Decimal(0)
    assert accepted[0].adjusted_close != accepted[0].close
    if asset_id == "wti_future":
        assert (accepted[0].open, accepted[0].high, accepted[0].low, accepted[0].close) == (-5, -2, -8, -3)


@pytest.mark.parametrize(("index", "value"), [
    (0, "unknown_asset"),
    (1, "unknown_provider"),
    (2, "GC=F"),
    (3, "unknown"),
    (3, ASSET_SOURCES["bitcoin_usd"].source_convention),
])
def test_rejects_unreviewed_mapping_even_without_bars(repository, index, value):
    mapping = list(_mapping_tuple("wti_future"))
    mapping[index] = value
    batch = _batch()
    provenance = dict(batch.provenance)
    provenance.update(zip(("asset_id", "source_id", "symbol", "source_convention"), mapping, strict=True))
    provenance["provider"] = provenance["source_id"]
    batch = replace(batch, bars=(), received_count=0, provenance=provenance)
    with pytest.raises(ValueError, match="unverified source convention"):
        repository._validate_batch(
            batch, MAPPING_ID, tuple(mapping), WINDOW.start, WINDOW.end_exclusive, _Counts(),
        )


def test_rejects_observation_with_incompatible_provider(repository):
    batch = _batch()
    batch = replace(batch, bars=(replace(batch.bars[0], provider="other"),))
    with pytest.raises(ValueError, match="incompatible provider or interval"):
        repository._validate_batch(
            batch, MAPPING_ID, _mapping_tuple("wti_future"), WINDOW.start, WINDOW.end_exclusive, _Counts(),
        )


def _connection_with_bar(bar, *, last_seen_at=RETRIEVED_AT):
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (
        bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.adjusted_close_basis,
        bar.volume, bar.retrieved_at, bar.source_convention, bar.session_status,
        bar.quality_status, 10, last_seen_at, 10,
    )
    return connection


@pytest.mark.parametrize("run_id", [9, 11])
@pytest.mark.parametrize(("stored_close", "incoming_close"), [(-3, -4), (-4, -3)])
def test_equal_timestamp_conflict_never_overwrites(repository, run_id, stored_close, incoming_close):
    bar = _batch().bars[0]
    connection = _connection_with_bar(replace(bar, close=Decimal(stored_close)))
    counts = _Counts()

    repository._save_bar(connection, MAPPING_ID, run_id, replace(bar, close=Decimal(incoming_close)), counts)

    assert counts == _Counts(rejected=1, diagnostics=["equal_timestamp_conflict:2026-10-05"])
    assert connection.execute.call_count == 1


@pytest.mark.parametrize("matches_last_seen", [False, True])
def test_conflict_is_detected_after_an_identical_later_sighting(repository, matches_last_seen):
    bar = _batch().bars[0]
    last_seen_at = RETRIEVED_AT + timedelta(hours=1)
    connection = _connection_with_bar(bar, last_seen_at=last_seen_at)
    counts = _Counts()
    conflict = replace(
        bar, close=Decimal(-4), retrieved_at=last_seen_at if matches_last_seen else RETRIEVED_AT,
    )

    repository._save_bar(connection, MAPPING_ID, 11, conflict, counts)

    assert counts == _Counts(rejected=1, diagnostics=["equal_timestamp_conflict:2026-10-05"])
    assert connection.execute.call_count == 1


def test_identical_reingestion_updates_only_last_seen(repository):
    bar = _batch().bars[0]
    connection = _connection_with_bar(bar)
    counts = _Counts()

    repository._save_bar(connection, MAPPING_ID, 11, bar, counts)

    assert counts == _Counts(unchanged=1)
    query, params = connection.execute.call_args.args
    assert "SET last_seen_at = %s, last_seen_run_id = %s" in query.as_string()
    assert params == (RETRIEVED_AT, 11, MAPPING_ID, bar.bar_date, bar.ohlc_basis)


def test_later_correction_replaces_optional_values_with_null(repository):
    bar = _batch().bars[0]
    connection = _connection_with_bar(bar)
    counts = _Counts()
    corrected = replace(
        bar, retrieved_at=RETRIEVED_AT + timedelta(hours=1),
        adjusted_close=None, adjusted_close_basis=None, volume=None,
    )

    repository._save_bar(connection, MAPPING_ID, 11, corrected, counts)

    assert counts == _Counts(corrected=1)
    query, params = connection.execute.call_args.args
    assert "adjusted_close_basis = %s, volume = %s" in query.as_string()
    assert params[4:8] == (None, None, None, corrected.retrieved_at)


def test_read_daily_reconstructs_rolling_without_inferring_exchange_times(repository, monkeypatch):
    bar = _batch().bars[0]
    connect = MagicMock()
    monkeypatch.setattr("backend.market_data.repository.psycopg.connect", connect)
    execute = connect.return_value.__enter__.return_value.execute
    execute.return_value.fetchall.return_value = [(
        MAPPING_ID, bar.asset_id, bar.source_id, bar.symbol, bar.bar_date, bar.interval,
        bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.volume, bar.ohlc_basis,
        bar.adjusted_close_basis, bar.retrieved_at, bar.session_status, bar.quality_status,
        bar.source_convention, bar.adapter_version, bar.provider_version, None, "unknown",
    )]

    result = repository.read_daily(MAPPING_ID, WINDOW)

    assert "mappings.rolling_method" in execute.call_args.args[0].as_string()
    assert result == (replace(bar, asset_source_id=MAPPING_ID, rolling_method="unknown"),)
    assert result[0].exchange_trade_date is result[0].settlement_time is result[0].availability_time is None
