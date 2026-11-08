"""Audit the Phase 2.5 snapshot in Supabase DEV without replaying ingestion."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import psycopg

from . import dev_import
from .backfill import DEFAULT_END, DEFAULT_START
from .historical_import import ImportValidationError, prepare_import


_COVERAGE = """
    SELECT m.id, m.symbol, count(*), min(b.bar_date), max(b.bar_date),
           count(b.adjusted_close),
           count(*) FILTER (WHERE b.adjusted_close <> b.close)
    FROM market_data.price_bars AS b
    JOIN market_data.asset_sources AS m ON m.id = b.asset_source_id
    GROUP BY m.id, m.symbol ORDER BY m.symbol
"""

_VIOLATIONS = """
    SELECT
        (SELECT count(*) FROM (
            SELECT 1 FROM market_data.price_bars
            GROUP BY asset_source_id, interval, bar_date, ohlc_basis HAVING count(*) > 1
        ) AS duplicates) AS duplicate_keys,
        count(*) FILTER (WHERE b.close IS NULL) AS missing_close,
        count(*) FILTER (WHERE ARRAY[b.open, b.high, b.low, b.close, b.adjusted_close, b.volume]
            && ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]) AS nonfinite,
        count(*) FILTER (WHERE b.high < greatest(b.low, b.open, b.close)
            OR b.low > least(b.open, b.close)) AS incoherent_ohlc,
        count(*) FILTER (WHERE a.sign_policy = 'strictly_positive'
            AND least(b.open, b.high, b.low, b.close, b.adjusted_close) <= 0) AS sign_violations,
        count(*) FILTER (WHERE b.volume < 0) AS negative_volume,
        count(*) FILTER (WHERE b.adjusted_close_basis IS DISTINCT FROM
            CASE WHEN b.adjusted_close IS NOT NULL THEN 'provider_reported' END) AS adjusted_basis,
        count(*) FILTER (WHERE b.bar_date < %s OR b.bar_date >= %s) AS outside_history,
        count(*) FILTER (WHERE m.id IS NULL OR a.id IS NULL OR s.id IS NULL
            OR r.id IS NULL OR seen.id IS NULL) AS orphan_bars,
        count(*) FILTER (WHERE (b.retrieved_at, b.run_id) > (b.last_seen_at, b.last_seen_run_id)
            OR b.source_convention IS DISTINCT FROM m.source_convention
            OR b.bar_date < greatest(r.start_date, seen.start_date)
            OR b.bar_date >= least(r.end_date_exclusive, seen.end_date_exclusive)) AS bad_provenance,
        (SELECT count(*) FROM market_data.ingestion_runs AS run
         LEFT JOIN market_data.asset_sources AS mapping ON mapping.id = run.asset_source_id
         WHERE mapping.id IS NULL) AS orphan_runs
    FROM market_data.price_bars AS b
    LEFT JOIN market_data.asset_sources AS m ON m.id = b.asset_source_id
    LEFT JOIN market_data.assets AS a ON a.id = m.asset_id
    LEFT JOIN market_data.data_sources AS s ON s.id = m.source_id
    LEFT JOIN market_data.ingestion_runs AS r
        ON (r.id, r.asset_source_id, r.interval) = (b.run_id, b.asset_source_id, b.interval)
    LEFT JOIN market_data.ingestion_runs AS seen
        ON (seen.id, seen.asset_source_id, seen.interval) = (b.last_seen_run_id, b.asset_source_id, b.interval)
"""

_RUNS = """
    SELECT r.id, m.symbol, r.start_date, r.end_date_exclusive, r.status,
           r.received_count, r.inserted_count, r.unchanged_count, r.corrected_count,
           r.stale_count, r.rejected_count, r.diagnostics, r.adapter_version, r.provider_version,
           r.interval = '1d' AND r.finished_at >= r.started_at AND r.error_code IS NULL
    FROM market_data.ingestion_runs AS r
    JOIN market_data.asset_sources AS m ON m.id = r.asset_source_id
    ORDER BY r.id
"""


def _check_runs(connection, exports):
    batches = {
        (export.mapping.symbol, prepared.window.start, prepared.window.end_exclusive): prepared.batch
        for export in exports for prepared in export.batches
    }
    runs = defaultdict(list)
    totals = [[0] * 6 for _ in range(2)]
    for run_id, symbol, start, end, status, *values in connection.execute(_RUNS):
        key = (symbol, start, end)
        batch = batches.get(key)
        if batch is None or len(runs[key]) >= 2:
            raise dev_import.DevImportError("unexpected ingestion run in the Phase 2.5 snapshot")
        replay = bool(runs[key])
        accepted, rejected = len(batch.bars), len(batch.rejected)
        counters = [batch.received_count, 0 if replay else accepted, accepted if replay else 0, 0, 0, rejected]
        diagnostics = [f"{item.position}:{item.code}" for item in batch.rejected]
        diagnostics.extend(f"warning:{warning}" for warning in batch.warnings)
        expected = [*counters, diagnostics, batch.provenance["adapter_version"], batch.provenance["provider_version"], True]
        if status != ("partial" if rejected else "succeeded") or values != expected:
            raise dev_import.DevImportError("run status, counters or archive diagnostics differ")
        runs[key].append(run_id)
        totals[replay] = [left + right for left, right in zip(totals[replay], counters, strict=True)]

    expected_links = {
        tuple(runs[key]): len(batch.bars) for key, batch in batches.items()
    }
    actual_links = {
        (origin, seen): count for origin, seen, count in connection.execute("""
            SELECT run_id, last_seen_run_id, count(*) FROM market_data.price_bars GROUP BY 1, 2
        """)
    }
    if any(len(ids) != 2 for ids in runs.values()) or runs.keys() != batches.keys() or actual_links != expected_links:
        raise dev_import.DevImportError("bar provenance must link the original run to its matching replay")
    return totals


def _audit(connection, exports):
    state = connection.execute("""
        SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation'),
               current_setting('server_version_num')::integer, transaction_timestamp()
    """).fetchone()
    if state[:2] != ("on", "repeatable read") or state[2] // 10000 != 17:
        raise dev_import.DevImportError("audit requires a repeatable read-only PostgreSQL 17 snapshot")
    dev_import._check_schema(connection)
    versions = dev_import._check_ledger(connection, pending_incremental=False)
    dev_import._check_catalog(connection, pending_incremental=False)
    counts, statuses, unresolved = dev_import._check_existing(connection, exports, require_complete=True)
    cursor = connection.execute(_VIOLATIONS, (DEFAULT_START, DEFAULT_END))
    violations = dict(zip((column.name for column in cursor.description), cursor.fetchone(), strict=True))
    if any(violations.values()) or unresolved:
        raise dev_import.DevImportError(f"audit found integrity violations: {violations}")
    totals = _check_runs(connection, exports)
    coverage = connection.execute(_COVERAGE).fetchall()
    wti = connection.execute("""
        SELECT b.open, b.high, b.low, b.close, b.adjusted_close, b.volume
        FROM market_data.price_bars AS b
        JOIN market_data.asset_sources AS m ON m.id = b.asset_source_id
        WHERE m.symbol = 'CL=F' AND m.source_id = 'yahoo_finance' AND b.bar_date = DATE '2020-04-20'
    """).fetchall()
    if len(wti) != 1 or wti[0][3] >= 0:
        raise dev_import.DevImportError("negative WTI close is missing")

    mappings = {symbol: mapping_id for mapping_id, symbol, *_ in coverage}
    print(f"Snapshot {state[3].isoformat()} | PostgreSQL {state[2]} | TLS verify-full | read-only")
    print(f"Migrations {', '.join(versions)} | bars {sum(counts.values())} | runs {statuses}")
    for _, symbol, count, first, last, adjusted, different in coverage:
        print(f"{symbol}: {count} | {first} .. {last} | adjusted present {adjusted}, different {different}")
    print(f"Integrity violations: {violations}")
    print("Run counters (received, inserted, unchanged, corrected, stale, rejected):", *totals)
    print("WTI 2020-04-20 (O/H/L/C/Adj Close/volume):", *(str(value) for value in wti[0]))

    # Probe access paths without executing the underlying SELECTs.
    for name, predicate, params in (
        ("history", "asset_source_id = %s ORDER BY bar_date", (mappings["BTC-USD"],)),
        ("range", "asset_source_id = %s AND bar_date >= %s AND bar_date < %s ORDER BY bar_date",
         (mappings["BTC-USD"], "2026-10-01", "2026-11-01")),
        ("latest", "asset_source_id = %s ORDER BY bar_date DESC LIMIT %s", (mappings["CL=F"], 20)),
        ("comparison", "asset_source_id IN (%s, %s) AND bar_date >= %s AND bar_date < %s "
         "ORDER BY bar_date, asset_source_id", (mappings["BTC-USD"], mappings["CL=F"], "2026-10-01", "2026-11-01")),
    ):
        print(f"EXPLAIN {name}")
        for (line,) in connection.execute(
            "EXPLAIN SELECT * FROM market_data.price_bars WHERE interval = '1d' AND " + predicate, params,
        ):
            print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--archive-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        exports = prepare_import(args.input_dir, args.archive_dir, initial_dataset=True)
        with dev_import.connect_dev(dev_import.configured_dev_dsn()) as connection:
            connection.read_only = True
            connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            _audit(connection, exports)
    except (dev_import.DevImportError, ImportValidationError, psycopg.Error, OSError, ValueError) as error:
        print(f"DEV AUDIT FAIL ({type(error).__name__})")
        if isinstance(error, dev_import.DevImportError):
            print(error)
        if isinstance(error, psycopg.Error):
            print(f"SQLSTATE: {error.sqlstate or 'unavailable'}")
        return 1
    print("DEV AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
