from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import psycopg
import pytest

from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data.migrations import apply_migrations
from backend.market_data.models import NormalizedBatch, RejectedObservation
from backend.market_data.pipeline import ingest_daily
from backend.market_data.repository import PostgresPriceRepository


DSN = os.environ.get("MARKET_DATA_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="MARKET_DATA_TEST_DSN is unset")
WINDOW = DateRange(date(2026, 10, 1), date(2026, 11, 1))
RETRIEVED_AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def schema():
    assert DSN is not None
    test_schema = "mdt_" + uuid4().hex[:20]
    result = apply_migrations(DSN, schema=test_schema)
    assert result.applied_versions == ("202611070001",)
    assert result.current_versions == ("202611070001",)
    assert (result.asset_count, result.source_count, result.mapping_count) == (6, 1, 6)
    return test_schema


@pytest.fixture
def repository(schema):
    return PostgresPriceRepository(DSN, schema=schema)


def _mapping_id(schema, asset_id="bitcoin_usd"):
    symbol = ASSET_SOURCES[asset_id].symbol
    with psycopg.connect(DSN) as connection:
        return connection.execute(
            f"SELECT id FROM {schema}.asset_sources WHERE source_id = %s AND symbol = %s",
            ("yahoo_finance", symbol),
        ).fetchone()[0]


def _raw_batch(*, retrieved_at=RETRIEVED_AT, close=10, adjusted_close=9.5, volume=4, open_price=9):
    mapping = ASSET_SOURCES["bitcoin_usd"]
    raw_mapping = replace(mapping)
    timestamp = int(datetime(2026, 10, 5, tzinfo=timezone.utc).timestamp())
    fields = {
        "Open": open_price,
        "High": max(value for value in (open_price, close) if value is not None) + 1,
        "Low": min(value for value in (open_price, close) if value is not None) - 1,
        "Close": close,
        "Adj Close": adjusted_close,
        "Volume": volume,
    }
    raw = RawBatch(
        asset_id="bitcoin_usd", mapping=raw_mapping, provider="yahoo_finance", ticker="BTC-USD",
        requested_range=WINDOW, interval="1d", retrieved_at=retrieved_at, adapter_version="1.0.0",
        rows=(RawRow(timestamp, fields),),
        temporal_metadata={"index_timezone": "UTC", "provider_session_label_convention": "unverified"},
        provider_version="1.8.0",
    )
    return raw, mapping


def _batch(*, retrieved_at=RETRIEVED_AT, close=10, adjusted_close=9.5, volume=4, open_price=9):
    raw, mapping = _raw_batch(
        retrieved_at=retrieved_at, close=close, adjusted_close=adjusted_close, volume=volume, open_price=open_price,
    )
    return normalize(raw, ASSETS["bitcoin_usd"], mapping, WINDOW)


def _run(repository, mapping_id, batch):
    run_id = repository.start_run(mapping_id, WINDOW)
    return repository.save_batch(run_id, batch)


def test_catalog_migration_integrity_and_initial_read(repository, schema):
    mapping_id = _mapping_id(schema)
    summary = _run(repository, mapping_id, _batch())
    assert summary.status == "succeeded"
    assert (summary.received_count, summary.inserted_count, summary.rejected_count) == (1, 1, 0)
    bars = repository.read_daily(mapping_id, WINDOW)
    assert len(bars) == 1
    assert bars[0].close == 10
    assert bars[0].provider == "yahoo_finance"
    assert bars[0].provider_version == "1.8.0"
    assert bars[0].asset_source_id == mapping_id


def test_synthetic_adapter_normalizer_repository_pipeline(repository, schema):
    raw, mapping = _raw_batch()

    class SyntheticAdapter:
        def fetch_daily(self, requested_mapping, window):
            assert requested_mapping == mapping and window == WINDOW
            return raw

    summary = ingest_daily(SyntheticAdapter(), repository, ASSETS["bitcoin_usd"], mapping, WINDOW)
    assert summary.status == "succeeded" and summary.inserted_count == 1
    mapping_id = repository.resolve_mapping_id(mapping)
    assert repository.read_daily(mapping_id, WINDOW)[0].close == 10


def test_acquisition_failure_finalizes_run_without_persisting_bars(repository, schema):
    mapping = ASSET_SOURCES["bitcoin_usd"]

    class FailingAdapter:
        def fetch_daily(self, requested_mapping, window):
            raise RuntimeError("synthetic acquisition failure")

    with pytest.raises(RuntimeError, match="synthetic acquisition failure"):
        ingest_daily(FailingAdapter(), repository, ASSETS["bitcoin_usd"], mapping, WINDOW)
    mapping_id = repository.resolve_mapping_id(mapping)
    assert repository.read_daily(mapping_id, WINDOW) == ()
    with psycopg.connect(DSN) as connection:
        row = connection.execute(
            f"SELECT status, error_code FROM {schema}.ingestion_runs WHERE asset_source_id = %s",
            (mapping_id,),
        ).fetchone()
    assert row == ("failed", "acquisition_failed")


def test_migration_rerun_is_noop_and_preserves_seed_counts(schema):
    result = apply_migrations(DSN, schema=schema)
    assert result.applied_versions == ()
    assert result.current_versions == ("202611070001",)
    assert (result.asset_count, result.source_count, result.mapping_count) == (6, 1, 6)


def test_identical_reingest_preserves_content_provenance_and_advances_last_seen(repository, schema):
    mapping_id = _mapping_id(schema)
    first = _run(repository, mapping_id, _batch())
    second = _run(repository, mapping_id, _batch(retrieved_at=RETRIEVED_AT.replace(hour=13)))
    assert first.inserted_count == 1
    assert second.unchanged_count == 1
    with psycopg.connect(DSN) as connection:
        row = connection.execute(
            f"SELECT run_id, last_seen_run_id FROM {schema}.price_bars WHERE asset_source_id = %s",
            (mapping_id,),
        ).fetchone()
    assert row == (first.run_id, second.run_id)


def test_historical_correction_can_replace_optional_fields_with_null(repository, schema):
    mapping_id = _mapping_id(schema)
    _run(repository, mapping_id, _batch())
    corrected = _run(repository, mapping_id, _batch(
        retrieved_at=RETRIEVED_AT.replace(hour=13), close=11, adjusted_close=None, volume=None, open_price=None,
    ))
    assert corrected.corrected_count == 1
    bar = repository.read_daily(mapping_id, WINDOW)[0]
    assert bar.close == 11 and bar.adjusted_close is None and bar.volume is None and bar.open is None
    assert bar.quality_status == "partial"


def test_older_lot_is_stale_and_does_not_overwrite_current_content(repository, schema):
    mapping_id = _mapping_id(schema)
    _run(repository, mapping_id, _batch(retrieved_at=RETRIEVED_AT.replace(hour=13)))
    old = _run(repository, mapping_id, _batch(retrieved_at=RETRIEVED_AT.replace(hour=11), close=12))
    assert old.stale_count == 1
    assert repository.read_daily(mapping_id, WINDOW)[0].close == 10


def test_concurrent_writers_keep_one_bar_and_reconcile_runs(repository, schema):
    mapping_id = _mapping_id(schema)

    def write():
        run_id = repository.start_run(mapping_id, WINDOW)
        return repository.save_batch(run_id, _batch())

    with ThreadPoolExecutor(max_workers=2) as executor:
        summaries = tuple(executor.map(lambda _: write(), range(2)))
    assert sum(item.inserted_count for item in summaries) == 1
    assert sum(item.unchanged_count + item.stale_count for item in summaries) == 1
    assert len(repository.read_daily(mapping_id, WINDOW)) == 1


def test_invalid_price_rolls_back_bars_and_records_failed_run(repository, schema):
    mapping_id = _mapping_id(schema)
    run_id = repository.start_run(mapping_id, WINDOW)
    invalid = _batch()
    invalid_bar = replace(
        invalid.bars[0], open=Decimal("-5"), high=Decimal("-2"), low=Decimal("-8"),
        close=Decimal("-4"), adjusted_close=Decimal("-3"),
    )
    invalid = replace(invalid, bars=(invalid_bar,))
    with pytest.raises(psycopg.errors.CheckViolation):
        repository.save_batch(run_id, invalid)
    assert repository.read_daily(mapping_id, WINDOW) == ()
    with psycopg.connect(DSN) as connection:
        run = connection.execute(
            f"SELECT status, received_count, inserted_count, error_code FROM {schema}.ingestion_runs WHERE id = %s",
            (run_id,),
        ).fetchone()
    assert run == ("failed", 1, 0, "persistence_failed")


def test_empty_batch_persists_empty_diagnostic(repository, schema):
    mapping_id = _mapping_id(schema)
    batch = _batch()
    empty = NormalizedBatch((), (), (), batch.provenance, 0)
    summary = _run(repository, mapping_id, empty)
    assert summary.status == "succeeded" and summary.diagnostics == ("empty_batch",)
    with psycopg.connect(DSN) as connection:
        diagnostics = connection.execute(
            f"SELECT diagnostics FROM {schema}.ingestion_runs WHERE id = %s", (summary.run_id,),
        ).fetchone()[0]
    assert diagnostics == ["empty_batch"]


def test_identical_duplicates_are_counted_and_conflicting_duplicates_rejected(repository, schema):
    mapping_id = _mapping_id(schema)
    original = _batch()
    duplicate = replace(original, bars=(original.bars[0], original.bars[0]), received_count=2)
    inserted = _run(repository, mapping_id, duplicate)
    assert (inserted.inserted_count, inserted.unchanged_count) == (1, 1)

    next_run_id = repository.start_run(mapping_id, WINDOW)
    changed = replace(
        original.bars[0], close=Decimal("11"), high=Decimal("12"), low=Decimal("9"),
    )
    conflicting = replace(original, bars=(original.bars[0], changed), received_count=2)
    rejected = repository.save_batch(next_run_id, conflicting)
    assert rejected.status == "partial" and rejected.rejected_count == 2
    assert "duplicate_conflict:2026-10-05" in rejected.diagnostics
    assert rejected.corrected_count == 0
    assert repository.read_daily(mapping_id, WINDOW)[0].close == 10


def test_all_rejected_batch_is_auditable(repository, schema):
    mapping_id = _mapping_id(schema)
    accepted = _batch()
    batch = NormalizedBatch((), (RejectedObservation(0, "missing_close"),), (), accepted.provenance, 1)
    summary = _run(repository, mapping_id, batch)
    assert summary.status == "partial" and summary.rejected_count == 1
    assert summary.diagnostics == ("0:missing_close",)
    assert repository.read_daily(mapping_id, WINDOW) == ()


def test_wti_negative_prices_are_allowed_and_asset_histories_are_isolated(repository, schema):
    btc_mapping_id = _mapping_id(schema)
    wti_mapping_id = _mapping_id(schema, "wti_future")
    convention = "verified:yahoo_exchange_date:v1:America/Chicago"
    with psycopg.connect(DSN) as connection:
        connection.execute(
            f"UPDATE {schema}.asset_sources SET source_convention = %s WHERE id = %s",
            (convention, wti_mapping_id),
        )
    run_id = repository.start_run(wti_mapping_id, WINDOW)
    with psycopg.connect(DSN) as connection:
        connection.execute(
            f"UPDATE {schema}.asset_sources SET source_convention = %s WHERE id = %s",
            (convention, wti_mapping_id),
        )
        connection.execute(
            f"INSERT INTO {schema}.price_bars "
            "(asset_source_id, interval, bar_date, ohlc_basis, open, high, low, close, volume, retrieved_at, "
            "source_convention, session_status, quality_status, run_id, last_seen_at, last_seen_run_id) "
            "VALUES (%s, '1d', '2026-10-05', 'provider_unadjusted', -5, 2, -8, -3, 0, %s, %s, "
            "'unknown', 'complete', %s, %s, %s)",
            (wti_mapping_id, RETRIEVED_AT, convention, run_id, RETRIEVED_AT, run_id),
        )
        connection.execute(
            f"UPDATE {schema}.ingestion_runs SET status = 'succeeded', finished_at = clock_timestamp(), "
            "received_count = 1, inserted_count = 1 WHERE id = %s", (run_id,),
        )
    assert repository.read_daily(wti_mapping_id, WINDOW)[0].close == -3
    assert repository.read_daily(btc_mapping_id, WINDOW) == ()


def test_histories_are_isolated_by_provider_for_the_same_asset(repository, schema):
    yahoo_mapping_id = _mapping_id(schema)
    with psycopg.connect(DSN) as connection:
        connection.execute(
            f"INSERT INTO {schema}.data_sources (id, name) VALUES ('fixture_source', 'Fixture Source')"
        )
        fixture_mapping_id = connection.execute(
            f"INSERT INTO {schema}.asset_sources "
            "(asset_id, source_id, symbol, source_convention) "
            "VALUES ('bitcoin_usd', 'fixture_source', 'BTC-USD', 'fixture_only') RETURNING id"
        ).fetchone()[0]
    run_id = repository.start_run(fixture_mapping_id, WINDOW)
    with psycopg.connect(DSN) as connection:
        connection.execute(
            f"INSERT INTO {schema}.price_bars "
            "(asset_source_id, interval, bar_date, ohlc_basis, open, high, low, close, retrieved_at, "
            "source_convention, session_status, quality_status, run_id, last_seen_at, last_seen_run_id) "
            "VALUES (%s, '1d', '2026-10-05', 'provider_unadjusted', 9, 11, 8, 10, %s, 'fixture_only', "
            "'unknown', 'complete', %s, %s, %s)",
            (fixture_mapping_id, RETRIEVED_AT, run_id, RETRIEVED_AT, run_id),
        )
        connection.execute(
            f"UPDATE {schema}.ingestion_runs SET status = 'succeeded', finished_at = clock_timestamp(), "
            "received_count = 1, inserted_count = 1 WHERE id = %s", (run_id,),
        )
    assert repository.read_daily(fixture_mapping_id, WINDOW)[0].provider == "fixture_source"
    assert repository.read_daily(yahoo_mapping_id, WINDOW) == ()


def test_sql_checks_reject_bad_foreign_keys_adjusted_basis_and_identity_edits(repository, schema):
    mapping_id = _mapping_id(schema)
    summary = _run(repository, mapping_id, _batch())
    with psycopg.connect(DSN) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    f"UPDATE {schema}.price_bars SET adjusted_close_basis = NULL WHERE asset_source_id = %s",
                    (mapping_id,),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    f"UPDATE {schema}.asset_sources SET symbol = 'OTHER' WHERE id = %s", (mapping_id,),
                )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    f"UPDATE {schema}.price_bars SET run_id = -1 WHERE asset_source_id = %s", (mapping_id,),
                )


def test_repository_rejects_bars_with_unverified_symbol_mapping(repository, schema):
    mapping_id = _mapping_id(schema, "wti_future")
    run_id = repository.start_run(mapping_id, WINDOW)
    batch = _batch()
    bad_bar = replace(batch.bars[0], asset_id="wti_future", source_id="yahoo_finance", symbol="CL=F",
                      source_convention="unknown")
    bad_provenance = dict(batch.provenance)
    bad_provenance.update(asset_id="wti_future", symbol="CL=F", source_convention="unknown")
    batch = NormalizedBatch((bad_bar,), (), (), bad_provenance, 1)
    with pytest.raises(ValueError, match="unverified source convention"):
        repository.save_batch(run_id, batch)
    assert repository.read_daily(mapping_id, WINDOW) == ()


def test_unverified_mappings_can_persist_withheld_rows_and_diagnostics(repository, schema):
    symbols = (
        ("gold_future", "America/Chicago"),
        ("wti_future", "America/Chicago"),
        ("sp500_index", "America/New_York"),
        ("world_equity_etf", "America/New_York"),
        ("emerging_markets_etf", "America/New_York"),
    )
    for asset_id, zone_name in symbols:
        mapping = ASSET_SOURCES[asset_id]
        raw = RawBatch(
            asset_id=asset_id, mapping=mapping, provider="yahoo_finance", ticker=mapping.symbol,
            requested_range=WINDOW, interval="1d", retrieved_at=RETRIEVED_AT, adapter_version="1.0.0",
            rows=(RawRow(int(datetime(2026, 10, 5, 12, tzinfo=timezone.utc).timestamp()), {
                "Open": 10, "High": 12, "Low": 9, "Close": 11,
            }),),
            temporal_metadata={"index_timezone": zone_name, "provider_session_label_convention": "unverified"},
            provider_version="1.8.0",
        )
        normalized = normalize(raw, ASSETS[asset_id], mapping, WINDOW)
        assert not normalized.bars
        assert normalized.rejected[0].code == "temporal_convention_unverified"
        mapping_id = _mapping_id(schema, asset_id)
        summary = _run(repository, mapping_id, normalized)
        assert summary.status == "partial" and summary.rejected_count == 1
        assert summary.diagnostics[0] == "0:temporal_convention_unverified"
        assert repository.read_daily(mapping_id, WINDOW) == ()
