from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import os
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data import migrations
from backend.market_data.migrations import apply_migrations
from backend.market_data.models import NormalizedBatch, RejectedObservation
from backend.market_data.pipeline import ingest_daily
from backend.market_data.repository import PostgresPriceRepository


DSN = os.environ.get("MARKET_DATA_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="MARKET_DATA_TEST_DSN is unset")
WINDOW = DateRange(date(2026, 10, 1), date(2026, 11, 1))
RETRIEVED_AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
VERSIONS = ("202611070001", "202611080001")


@pytest.fixture
def schema():
    assert DSN is not None
    test_schema = "mdt_" + uuid4().hex[:20]
    result = apply_migrations(DSN, schema=test_schema)
    assert result.applied_versions == VERSIONS
    assert result.current_versions == VERSIONS
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


def test_daily_queries_preserve_observations_and_instrument_units(repository, schema):
    btc_id = _mapping_id(schema)
    wti_id = _mapping_id(schema, "wti_future")
    friday, saturday, monday = (date(2026, 10, day) for day in (2, 3, 5))
    btc = _batch()
    btc_bar = btc.bars[0]
    assert _run(repository, btc_id, replace(
        btc, bars=(btc_bar, replace(btc_bar, bar_date=friday), replace(btc_bar, bar_date=saturday)),
        received_count=3,
    )).inserted_count == 3

    wti = ASSET_SOURCES["wti_future"]
    wti_raw = RawBatch(
        asset_id=wti.asset_id, mapping=wti, provider=wti.source_id, ticker=wti.symbol,
        requested_range=WINDOW, interval="1d", retrieved_at=RETRIEVED_AT, adapter_version="1.0.0",
        rows=(RawRow(int(datetime(2026, 10, 2, 4, tzinfo=timezone.utc).timestamp()), {
            "Open": -5, "High": -2, "Low": -8, "Close": -3, "Adj Close": -4, "Volume": 0,
        }),),
        temporal_metadata={"index_timezone": "America/New_York"},
    )
    assert _run(repository, wti_id, normalize(wti_raw, ASSETS[wti.asset_id], wti, WINDOW)).inserted_count == 1

    # A registered but unverified mapping is excluded from the enabled catalog.
    with psycopg.connect(DSN) as connection:
        connection.execute(f"""
            INSERT INTO {schema}.asset_sources (asset_id, source_id, symbol, source_convention)
            VALUES ('bitcoin_usd', 'yahoo_finance', 'UNVERIFIED', 'unknown')
        """)
    assets = {entry.symbol: entry for entry in repository.list_available_assets()}
    assert len(assets) == 6 and "UNVERIFIED" not in assets
    btc_asset = assets["BTC-USD"]
    assert (btc_asset.asset_source_id, btc_asset.provider_name, btc_asset.economic_unit) == (
        btc_id, "Yahoo Finance", "USD/BTC",
    )
    assert (btc_asset.first_date, btc_asset.last_date, btc_asset.bar_count) == (friday, monday, 3)
    assert (assets["VT"].first_date, assets["VT"].last_date, assets["VT"].bar_count) == (None, None, 0)

    history = repository.read_daily(btc_id)
    assert [bar.bar_date for bar in history] == [friday, saturday, monday]
    assert history[0].close == Decimal("10")
    assert history[0].adjusted_close == Decimal("9.5")
    assert history[0].retrieved_at == RETRIEVED_AT
    window = DateRange(friday, monday)
    assert repository.read_daily(btc_id, window) == history[:2]
    assert repository.read_latest_daily(btc_id, 2) == history[-2:]
    assert repository.read_latest_daily(btc_id, 1000) == history
    assert repository.read_daily(btc_id, DateRange(date(2026, 10, 6), date(2026, 10, 7))) == ()
    unknown_id = 2**63 - 1
    assert repository.read_daily(unknown_id) == repository.read_latest_daily(unknown_id, 1) == ()

    points = repository.compare_daily(btc_id, wti_id, window)
    assert [point.bar_date for point in points] == [friday, friday, saturday]
    assert [(p.bar_date, p.close, p.economic_unit) for p in points if p.asset_source_id == wti_id] == [
        (friday, Decimal("-3"), "USD/bbl"),
    ]
    assert [(p.bar_date, p.close, p.economic_unit) for p in points if p.asset_source_id == btc_id] == [
        (friday, Decimal("10"), "USD/BTC"), (saturday, Decimal("10"), "USD/BTC"),
    ]
    assert repository.compare_daily(btc_id, unknown_id, window) == tuple(
        point for point in points if point.asset_source_id == btc_id
    )


def test_catalog_migration_integrity_and_initial_read(repository, schema):
    with psycopg.connect(DSN) as connection:
        mappings = connection.execute(
            f"SELECT asset_id, source_id, symbol, source_convention, rolling_method FROM {schema}.asset_sources"
        ).fetchall()
    assert set(mappings) == {
        (mapping.asset_id, mapping.source_id, mapping.symbol, mapping.source_convention, mapping.rolling_method)
        for mapping in ASSET_SOURCES.values()
    }
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
    assert result.current_versions == VERSIONS
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
    mapping = ASSET_SOURCES["wti_future"]
    wti_mapping_id = repository.resolve_mapping_id(mapping)
    raw = RawBatch(
        asset_id=mapping.asset_id, mapping=mapping, provider=mapping.source_id, ticker=mapping.symbol,
        requested_range=WINDOW, interval="1d", retrieved_at=RETRIEVED_AT, adapter_version="1.0.0",
        rows=(RawRow(int(datetime(2026, 10, 5, 4, tzinfo=timezone.utc).timestamp()), {
            "Open": -5, "High": -2, "Low": -8, "Close": -3, "Volume": 0,
        }),),
        temporal_metadata={"index_timezone": "America/New_York"},
    )
    batch = normalize(raw, ASSETS[mapping.asset_id], mapping, WINDOW)
    assert _run(repository, wti_mapping_id, batch).inserted_count == 1
    bar = repository.read_daily(wti_mapping_id, WINDOW)[0]
    assert (bar.close, bar.adjusted_close, bar.volume, bar.rolling_method) == (-3, None, 0, "unknown")
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
    with psycopg.connect(DSN) as connection:
        connection.execute(
            f"UPDATE {schema}.asset_sources SET source_convention = 'unknown' WHERE id = %s",
            (mapping_id,),
        )
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


def test_verified_mappings_can_record_withheld_rows_and_diagnostics(repository, schema):
    for asset_id, mapping in ASSET_SOURCES.items():
        if asset_id == "bitcoin_usd":
            continue
        raw = RawBatch(
            asset_id=asset_id, mapping=mapping, provider="yahoo_finance", ticker=mapping.symbol,
            requested_range=WINDOW, interval="1d", retrieved_at=RETRIEVED_AT, adapter_version="1.0.0",
            rows=(RawRow(int(datetime(2026, 10, 5, 12, tzinfo=timezone.utc).timestamp()), {
                "Open": 10, "High": 12, "Low": 9, "Close": 11,
            }),),
            temporal_metadata={"index_timezone": "America/Chicago", "provider_session_label_convention": "unverified"},
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


@pytest.mark.parametrize("asset_id", ASSET_SOURCES)
def test_catalog_asset_round_trip(repository, asset_id):
    mapping = ASSET_SOURCES[asset_id]
    raw, _ = _raw_batch()
    zone, hour, minute = ("UTC", 0, 0) if asset_id == "bitcoin_usd" else ("America/New_York", 13, 30)
    label = datetime(2026, 10, 5, hour, minute, tzinfo=timezone.utc)
    raw = replace(
        raw, asset_id=asset_id, mapping=mapping, ticker=mapping.symbol,
        rows=(RawRow(int(label.timestamp()), raw.rows[0].fields),),
        temporal_metadata={"index_timezone": zone},
    )
    batch = normalize(raw, ASSETS[asset_id], mapping, WINDOW)
    mapping_id = repository.resolve_mapping_id(mapping)
    assert _run(repository, mapping_id, batch).inserted_count == 1
    (bar,) = repository.read_daily(mapping_id, WINDOW)
    assert (bar.asset_id, bar.source_id, bar.provider, bar.symbol) == (
        asset_id, mapping.source_id, mapping.source_id, mapping.symbol,
    )
    assert (bar.asset_source_id, bar.interval, bar.source_convention, bar.rolling_method) == (
        mapping_id, "1d", mapping.source_convention, mapping.rolling_method,
    )
    assert (bar.bar_date, bar.retrieved_at, bar.adapter_version, bar.provider_version) == (
        label.date(), RETRIEVED_AT, "1.0.0", "1.8.0",
    )
    assert (bar.close, bar.adjusted_close, bar.adjusted_close_basis) == (10, Decimal("9.5"), "provider_reported")


@pytest.mark.parametrize("changes", [
    {"high": Decimal("9")},
    {"volume": Decimal("-1")},
], ids=["invalid-ohlc", "negative-volume"])
def test_sql_rejection_rolls_back_prior_bar_in_batch(repository, schema, changes):
    mapping_id = _mapping_id(schema)
    run_id = repository.start_run(mapping_id, WINDOW)
    batch = _batch()
    (valid,) = batch.bars
    invalid = replace(valid, bar_date=date(2026, 10, 6), **changes)
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        repository.save_batch(run_id, replace(batch, bars=(valid, invalid), received_count=2))
    assert caught.value.diag.table_name == "price_bars"
    assert repository.read_daily(mapping_id, WINDOW) == ()
    with psycopg.connect(DSN) as connection:
        run = connection.execute(
            f"SELECT status, received_count, inserted_count, error_code, finished_at IS NOT NULL "
            f"FROM {schema}.ingestion_runs WHERE id = %s", (run_id,),
        ).fetchone()
    assert run == ("failed", 2, 0, "persistence_failed", True)


def test_sql_requires_close(repository, schema):
    mapping_id = _mapping_id(schema)
    _run(repository, mapping_id, _batch())
    with pytest.raises(psycopg.errors.NotNullViolation) as caught:
        with psycopg.connect(DSN) as connection:
            connection.execute(f"UPDATE {schema}.price_bars SET close = NULL")
    assert caught.value.diag.column_name == "close"
    assert repository.read_daily(mapping_id, WINDOW)[0].close == 10


def test_exact_replay_and_equal_timestamp_conflict(repository, schema):
    mapping_id = _mapping_id(schema)
    batch = _batch()
    first = _run(repository, mapping_id, batch)
    replay = _run(repository, mapping_id, batch)
    conflict = _run(repository, mapping_id, _batch(close=11))
    assert (first.inserted_count, replay.unchanged_count) == (1, 1)
    assert (conflict.status, conflict.rejected_count, conflict.corrected_count) == ("partial", 1, 0)
    assert "equal_timestamp_conflict:2026-10-05" in conflict.diagnostics
    (bar,) = repository.read_daily(mapping_id, WINDOW)
    assert (bar.close, bar.retrieved_at, bar.provider_version) == (10, RETRIEVED_AT, "1.8.0")
    with psycopg.connect(DSN) as connection:
        bars = connection.execute(
            f"SELECT run_id, last_seen_run_id, retrieved_at, last_seen_at FROM {schema}.price_bars"
        ).fetchall()
        runs = connection.execute(
            f"SELECT id, status, adapter_version, provider_version, received_count, inserted_count, "
            f"unchanged_count, rejected_count, finished_at >= started_at FROM {schema}.ingestion_runs ORDER BY id"
        ).fetchall()
    assert bars == [(first.run_id, replay.run_id, RETRIEVED_AT, RETRIEVED_AT)]
    assert runs == [
        (first.run_id, "succeeded", "1.0.0", "1.8.0", 1, 1, 0, 0, True),
        (replay.run_id, "succeeded", "1.0.0", "1.8.0", 1, 0, 1, 0, True),
        (conflict.run_id, "partial", "1.0.0", "1.8.0", 1, 0, 0, 1, True),
    ]


def test_concurrent_runs_preserve_content_and_last_seen_provenance(repository, schema):
    mapping_id = _mapping_id(schema)
    run_ids = [repository.start_run(mapping_id, WINDOW) for _ in range(2)]
    ready = Barrier(2, timeout=10)
    batch = _batch()

    def write(run_id):
        ready.wait()
        return repository.save_batch(run_id, batch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        summaries = tuple(executor.map(write, run_ids))
    (inserted,) = [summary for summary in summaries if summary.inserted_count == 1]
    assert all(summary.status == "succeeded" for summary in summaries)
    with psycopg.connect(DSN) as connection:
        bars = connection.execute(
            f"SELECT close, run_id, last_seen_run_id FROM {schema}.price_bars"
        ).fetchall()
    assert bars == [(10, inserted.run_id, max(run_ids))]


@pytest.fixture
def initial_schema(monkeypatch):
    name = "mdt_" + uuid4().hex[:20]
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_MARKET_DATA_MIGRATIONS", migrations._MARKET_DATA_MIGRATIONS[:1])
        assert apply_migrations(DSN, schema=name).applied_versions == VERSIONS[:1]
    return name


def _mapping_rows(connection, schema):
    return connection.execute(f"SELECT * FROM {schema}.asset_sources ORDER BY id").fetchall()


def test_incremental_upgrade_preserves_catalog_and_records_checksums(initial_schema):
    schema = initial_schema
    with psycopg.connect(DSN) as connection:
        before = _mapping_rows(connection, schema)
    result = apply_migrations(DSN, schema=schema)
    assert result.applied_versions == VERSIONS[1:]
    assert result.current_versions == VERSIONS
    with psycopg.connect(DSN) as connection:
        after = _mapping_rows(connection, schema)
        assert after == [
            (*row[:4], ASSET_SOURCES[row[1]].source_convention, *row[5:]) for row in before
        ]
        assert connection.execute(
            f"SELECT sign_policy FROM {schema}.assets WHERE id = 'wti_future'"
        ).fetchone() == ("signed_allowed",)
        assert {row[1]: row[6] for row in after if row[1] in ("gold_future", "wti_future")} == {
            "gold_future": "unknown", "wti_future": "unknown",
        }
        log_query = f"SELECT version, checksum_sha256, applied_at FROM {schema}.schema_migrations ORDER BY version"
        log = connection.execute(log_query).fetchall()
        paths = [migrations._MIGRATIONS / name for name in migrations._MARKET_DATA_MIGRATIONS]
        assert [(version, checksum) for version, checksum, _ in log] == [
            (version, sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest())
            for version, path in zip(VERSIONS, paths, strict=True)
        ]
        assert all(applied_at is not None for _, _, applied_at in log)
        connection.execute(paths[1].read_text(encoding="utf-8").replace("market_data", schema))
        assert _mapping_rows(connection, schema) == after
        assert connection.execute(log_query).fetchall() == log
    assert apply_migrations(DSN, schema=schema).applied_versions == ()


@pytest.mark.parametrize(("change", "message"), [
    ("UPDATE {schema}.asset_sources SET source_convention = 'unexpected' WHERE symbol = 'VWO'", "convention"),
    ("DELETE FROM {schema}.asset_sources WHERE symbol = 'VWO'", "identity"),
])
def test_incremental_upgrade_rejects_unexpected_state_atomically(initial_schema, change, message):
    schema = initial_schema
    with psycopg.connect(DSN) as connection:
        connection.execute(change.format(schema=schema))
        before = _mapping_rows(connection, schema)
    with pytest.raises(psycopg.errors.RaiseException, match=f"unexpected Yahoo mapping {message} for VWO"):
        apply_migrations(DSN, schema=schema)
    with psycopg.connect(DSN) as connection:
        assert _mapping_rows(connection, schema) == before
        assert migrations.inspect_schema(connection, schema=schema).current_versions == VERSIONS[:1]


def test_migration_runner_rejects_checksum_mismatch(schema):
    with psycopg.connect(DSN) as connection:
        connection.execute(
            f"UPDATE {schema}.schema_migrations SET checksum_sha256 = %s WHERE version = %s",
            ("0" * 64, VERSIONS[1]),
        )
    with pytest.raises(RuntimeError, match=f"migration checksum changed after application: {VERSIONS[1]}"):
        apply_migrations(DSN, schema=schema)


def test_schema_contains_persistence_indexes_and_triggers(schema):
    with psycopg.connect(DSN) as connection:
        indexes = connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = %s", (schema,),
        ).fetchall()
        triggers = connection.execute(
            "SELECT DISTINCT trigger_name FROM information_schema.triggers WHERE trigger_schema = %s", (schema,),
        ).fetchall()
        constraints = connection.execute(
            "SELECT DISTINCT constraint_type FROM information_schema.table_constraints "
            "WHERE table_schema = %s AND table_name = 'price_bars'", (schema,),
        ).fetchall()
    assert {name for (name,) in indexes} >= {
        "asset_sources_asset_idx", "ingestion_runs_source_started_idx",
        "price_bars_run_idx", "price_bars_last_seen_run_idx",
    }
    assert {name for (name,) in triggers} == {
        "price_bars_sign_policy", "assets_sign_policy_guard", "asset_source_identity_guard",
    }
    assert {kind for (kind,) in constraints} >= {"CHECK", "PRIMARY KEY", "FOREIGN KEY"}
