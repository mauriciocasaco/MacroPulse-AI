"""PostgreSQL persistence for normalized daily bars."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .catalog import ASSET_SOURCES
from .models import AssetSource, DateRange, NormalizedBatch, PriceBar1d, RunSummary


MAX_LATEST_DAILY = 1000


@dataclass(frozen=True)
class AvailableAsset:
    asset_source_id: int
    asset_id: str
    name: str
    source_id: str
    provider_name: str
    symbol: str
    economic_unit: str
    calendar_code: str
    first_date: date | None
    last_date: date | None
    bar_count: int


@dataclass(frozen=True)
class ComparisonPoint:
    asset_source_id: int
    asset_id: str
    source_id: str
    symbol: str
    economic_unit: str
    bar_date: date
    close: Decimal
    adjusted_close: Decimal | None = None
    adjusted_close_basis: str | None = None
    ohlc_basis: str | None = None
    session_status: str | None = None
    source_convention: str | None = None
    rolling_method: str | None = None
    retrieved_at: datetime | None = None


def _validate_mapping_id(mapping_id: int) -> None:
    if type(mapping_id) is not int or not 1 <= mapping_id <= 2**63 - 1:
        raise ValueError("mapping_id must be a positive PostgreSQL bigint")


class PriceRepository(Protocol):
    def resolve_mapping_id(self, mapping: AssetSource) -> int: ...
    def start_run(self, mapping_id: int, window: DateRange) -> int: ...
    def save_batch(self, run_id: int, batch: NormalizedBatch) -> RunSummary: ...
    def fail_run(self, run_id: int, code: str) -> RunSummary: ...
    def read_daily(self, mapping_id: int, window: DateRange) -> tuple[PriceBar1d, ...]: ...


@dataclass
class _Counts:
    inserted: int = 0
    unchanged: int = 0
    corrected: int = 0
    stale: int = 0
    rejected: int = 0
    diagnostics: list[str] = field(default_factory=list)


class PostgresPriceRepository:
    """Use one short transaction per run operation and per saved batch."""

    def __init__(
        self, dsn: str, *, schema: str = "market_data",
        connect: Callable[[str], psycopg.Connection] | None = None,
    ) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("schema must be a lowercase PostgreSQL identifier")
        self._dsn = dsn
        self._schema = sql.Identifier(schema)
        self._connection_factory = connect

    def _connect(self) -> psycopg.Connection:
        return (self._connection_factory or psycopg.connect)(self._dsn)

    def resolve_mapping_id(self, mapping: AssetSource) -> int:
        asset_id = getattr(mapping, "asset_id", None)
        source_id = getattr(mapping, "source_id", None)
        symbol = getattr(mapping, "symbol", None)
        if not all(isinstance(value, str) and value for value in (asset_id, source_id, symbol)):
            raise ValueError("asset source mapping must include asset, source, and symbol identities")
        with self._connect() as connection:
            row = connection.execute(sql.SQL(
                "SELECT id FROM {}.asset_sources WHERE asset_id = %s AND source_id = %s AND symbol = %s"
            ).format(self._schema), (asset_id, source_id, symbol)).fetchone()
        if row is None:
            raise ValueError("asset source mapping is not registered")
        return row[0]

    def start_run(self, mapping_id: int, window: DateRange) -> int:
        with self._connect() as connection:
            row = connection.execute(sql.SQL(
                "INSERT INTO {}.ingestion_runs "
                "(asset_source_id, interval, start_date, end_date_exclusive, status, adapter_version) "
                "VALUES (%s, '1d', %s, %s, 'running', 'unknown') RETURNING id"
            ).format(self._schema), (mapping_id, window.start, window.end_exclusive)).fetchone()
        return row[0]

    def save_batch(self, run_id: int, batch: NormalizedBatch) -> RunSummary:
        try:
            with self._connect() as connection:
                with connection.transaction():
                    run = connection.execute(sql.SQL(
                        "SELECT asset_source_id, start_date, end_date_exclusive, status "
                        "FROM {}.ingestion_runs WHERE id = %s FOR UPDATE"
                    ).format(self._schema), (run_id,)).fetchone()
                    if run is None or run[3] != "running":
                        raise ValueError("ingestion run is missing or no longer running")
                    mapping_id, start_date, end_date, _ = run
                    mapping = connection.execute(sql.SQL(
                        "SELECT mappings.asset_id, mappings.source_id, mappings.symbol, mappings.source_convention "
                        "FROM {}.asset_sources AS mappings WHERE mappings.id = %s FOR UPDATE"
                    ).format(self._schema), (mapping_id,)).fetchone()
                    if mapping is None:
                        raise ValueError("ingestion mapping no longer exists")

                    counts = _Counts(rejected=len(batch.rejected))
                    accepted = self._validate_batch(batch, mapping_id, mapping, start_date, end_date, counts)
                    for bar in accepted:
                        self._save_bar(connection, mapping_id, run_id, bar, counts)
                    if batch.received_count != (
                        counts.inserted + counts.unchanged + counts.corrected + counts.stale + counts.rejected
                    ):
                        raise ValueError("normalized batch row counts do not reconcile")
                    status = "partial" if counts.rejected else "succeeded"
                    diagnostics = [f"{item.position}:{item.code}" for item in batch.rejected]
                    diagnostics.extend(f"warning:{warning}" for warning in batch.warnings)
                    diagnostics.extend(counts.diagnostics)
                    if batch.received_count == 0:
                        diagnostics.append("empty_batch")
                    connection.execute(sql.SQL(
                        "UPDATE {}.ingestion_runs SET status = %s, finished_at = clock_timestamp(), "
                        "adapter_version = %s, provider_version = %s, "
                        "received_count = %s, inserted_count = %s, unchanged_count = %s, corrected_count = %s, "
                        "stale_count = %s, rejected_count = %s, diagnostics = %s WHERE id = %s"
                    ).format(self._schema), (
                        status, str(batch.provenance.get("adapter_version") or "unknown"),
                        batch.provenance.get("provider_version"), batch.received_count, counts.inserted, counts.unchanged, counts.corrected,
                        counts.stale, counts.rejected, Jsonb(diagnostics), run_id,
                    ))
            return RunSummary(run_id, status, batch.received_count, counts.inserted, counts.unchanged,
                              counts.corrected, counts.stale, counts.rejected, diagnostics=tuple(diagnostics))
        except Exception as persistence_error:
            try:
                self._fail_run_after_rollback(run_id, "persistence_failed", batch.received_count)
            except Exception:
                persistence_error.add_note("could not record the failed ingestion state")
            raise

    def fail_run(self, run_id: int, code: str) -> RunSummary:
        safe_code = _safe_error_code(code)
        with self._connect() as connection:
            row = connection.execute(sql.SQL(
                "UPDATE {}.ingestion_runs SET status = 'failed', finished_at = clock_timestamp(), "
                "error_code = %s WHERE id = %s AND status = 'running' "
                "RETURNING received_count, inserted_count, unchanged_count, corrected_count, stale_count, rejected_count"
            ).format(self._schema), (safe_code, run_id)).fetchone()
            if row is None:
                raise ValueError("ingestion run is missing or no longer running")
        return RunSummary(run_id, "failed", *row, error_code=safe_code)

    def list_available_assets(self) -> tuple[AvailableAsset, ...]:
        """List verified mappings, including those with no stored daily bars."""
        with self._connect() as connection:
            rows = connection.execute(sql.SQL("""
                SELECT m.id, a.id, a.name, s.id, s.name, m.symbol, a.economic_unit,
                       a.calendar_code, min(b.bar_date), max(b.bar_date), count(b.bar_date)
                FROM {schema}.asset_sources AS m
                JOIN {schema}.assets AS a ON a.id = m.asset_id
                JOIN {schema}.data_sources AS s ON s.id = m.source_id
                LEFT JOIN {schema}.price_bars AS b
                    ON b.asset_source_id = m.id AND b.interval = '1d'
                WHERE starts_with(m.source_convention, 'verified:')
                GROUP BY m.id, a.id, s.id
                ORDER BY a.id, s.id, m.symbol
            """).format(schema=self._schema)).fetchall()
        return tuple(AvailableAsset(*row) for row in rows)

    def read_daily(self, mapping_id: int, window: DateRange | None = None) -> tuple[PriceBar1d, ...]:
        """Read chart-ready daily bars, optionally restricted to [start, end_exclusive)."""
        _validate_mapping_id(mapping_id)
        if window is None:
            return self._read_daily(mapping_id, sql.SQL("ORDER BY bars.bar_date"), ())
        if not isinstance(window, DateRange):
            raise ValueError("window must be a DateRange")
        return self._read_daily(mapping_id, sql.SQL(
            "AND bars.bar_date >= %s AND bars.bar_date < %s ORDER BY bars.bar_date"
        ), (window.start, window.end_exclusive))

    def read_latest_daily(self, mapping_id: int, limit: int) -> tuple[PriceBar1d, ...]:
        """Return up to limit stored observations in ascending date order."""
        _validate_mapping_id(mapping_id)
        if type(limit) is not int or not 1 <= limit <= MAX_LATEST_DAILY:
            raise ValueError(f"limit must be an integer from 1 to {MAX_LATEST_DAILY}")
        bars = self._read_daily(mapping_id, sql.SQL("ORDER BY bars.bar_date DESC LIMIT %s"), (limit,))
        return bars[::-1]

    def compare_daily(
        self, first_mapping_id: int, second_mapping_id: int, window: DateRange,
    ) -> tuple[ComparisonPoint, ...]:
        """Read two mappings in one query, preserving close bases, units and provenance."""
        _validate_mapping_id(first_mapping_id)
        _validate_mapping_id(second_mapping_id)
        if first_mapping_id == second_mapping_id:
            raise ValueError("comparison requires two distinct mapping IDs")
        if not isinstance(window, DateRange):
            raise ValueError("window must be a DateRange")
        with self._connect() as connection:
            rows = connection.execute(sql.SQL("""
                SELECT m.id, m.asset_id, m.source_id, m.symbol, a.economic_unit, b.bar_date, b.close,
                       b.adjusted_close, b.adjusted_close_basis, b.ohlc_basis, b.session_status,
                       b.source_convention, m.rolling_method, b.retrieved_at
                FROM {schema}.price_bars AS b
                JOIN {schema}.asset_sources AS m ON m.id = b.asset_source_id
                JOIN {schema}.assets AS a ON a.id = m.asset_id
                WHERE b.asset_source_id IN (%s, %s) AND b.interval = '1d'
                  AND b.bar_date >= %s AND b.bar_date < %s
                ORDER BY b.bar_date, b.asset_source_id
            """).format(schema=self._schema), (
                first_mapping_id, second_mapping_id, window.start, window.end_exclusive,
            )).fetchall()
        return tuple(ComparisonPoint(*row[:-1], row[-1].astimezone(timezone.utc)) for row in rows)

    def _read_daily(
        self, mapping_id: int, suffix: sql.SQL, params: tuple[date | int, ...],
    ) -> tuple[PriceBar1d, ...]:
        with self._connect() as connection:
            rows = connection.execute(sql.SQL(
                "SELECT bars.asset_source_id, mappings.asset_id, mappings.source_id, mappings.symbol, "
                "bars.bar_date, bars.interval, bars.open, bars.high, bars.low, bars.close, "
                "bars.adjusted_close, bars.volume, bars.ohlc_basis, bars.adjusted_close_basis, "
                "bars.retrieved_at, bars.session_status, bars.quality_status, bars.source_convention, "
                "runs.adapter_version, runs.provider_version, mappings.volume_unit, mappings.rolling_method "
                "FROM {}.price_bars AS bars "
                "JOIN {}.asset_sources AS mappings ON mappings.id = bars.asset_source_id "
                "JOIN {}.ingestion_runs AS runs ON runs.id = bars.run_id "
                "WHERE bars.asset_source_id = %s AND bars.interval = '1d' "
                "{}"
            ).format(self._schema, self._schema, self._schema, suffix),
                (mapping_id, *params)).fetchall()
        return tuple(PriceBar1d(
            asset_source_id=row[0], asset_id=row[1], source_id=row[2], symbol=row[3], bar_date=row[4],
            interval=row[5], open=row[6], high=row[7], low=row[8], close=row[9], adjusted_close=row[10],
            volume=row[11], ohlc_basis=row[12], adjusted_close_basis=row[13],
            retrieved_at=row[14].astimezone(timezone.utc), session_status=row[15], quality_status=row[16],
            source_convention=row[17], provider=row[2], adapter_version=row[18], provider_version=row[19],
            volume_unit=row[20], rolling_method=row[21],
        ) for row in rows)

    def _validate_batch(self, batch: NormalizedBatch, mapping_id: int, mapping: tuple[object, ...], start_date, end_date, counts: _Counts) -> tuple[PriceBar1d, ...]:
        if batch.received_count != len(batch.bars) + len(batch.rejected):
            raise ValueError("normalized batch received count does not match its rows and diagnostics")
        asset_id, source_id, symbol, source_convention = mapping
        if (batch.provenance.get("asset_id") != asset_id or batch.provenance.get("source_id") != source_id
                or batch.provenance.get("provider") != source_id):
            raise ValueError("normalized batch provenance does not match ingestion mapping")
        if batch.provenance.get("symbol") != symbol or batch.provenance.get("source_convention") != source_convention:
            raise ValueError("normalized batch provenance does not match ingestion mapping")
        if batch.provenance.get("requested_start") != start_date or batch.provenance.get("requested_end_exclusive") != end_date:
            raise ValueError("normalized batch range does not match ingestion run")
        if not _is_verified_convention(asset_id, source_id, symbol, source_convention):
            raise ValueError("repository refuses to persist bars with an unverified source convention")
        candidates: dict[tuple[object, ...], list[PriceBar1d]] = defaultdict(list)
        for bar in batch.bars:
            if (bar.asset_id, bar.source_id, bar.symbol, bar.source_convention) != (
                asset_id, source_id, symbol, source_convention
            ):
                raise ValueError("normalized observation does not match ingestion mapping")
            if bar.provider != source_id or bar.interval != "1d":
                raise ValueError("normalized observation has an incompatible provider or interval")
            if bar.asset_source_id is not None and bar.asset_source_id != mapping_id:
                raise ValueError("normalized observation has a different asset source id")
            if not start_date <= bar.bar_date < end_date:
                raise ValueError("normalized observation is outside the ingestion range")
            candidates[(bar.interval, bar.bar_date, bar.ohlc_basis)].append(bar)

        accepted: list[PriceBar1d] = []
        for rows in candidates.values():
            if all(_bar_content(rows[0]) == _bar_content(row) for row in rows[1:]):
                accepted.append(rows[0])
                counts.unchanged += len(rows) - 1
            else:
                counts.rejected += len(rows)
                counts.diagnostics.append(f"duplicate_conflict:{rows[0].bar_date.isoformat()}")
        return tuple(accepted)

    def _save_bar(self, connection: psycopg.Connection, mapping_id: int, run_id: int, bar: PriceBar1d, counts: _Counts) -> None:
        current = connection.execute(sql.SQL(
            "SELECT open, high, low, close, adjusted_close, adjusted_close_basis, volume, retrieved_at, "
            "source_convention, session_status, quality_status, run_id, last_seen_at, last_seen_run_id "
            "FROM {}.price_bars WHERE asset_source_id = %s AND interval = '1d' "
            "AND bar_date = %s AND ohlc_basis = %s FOR UPDATE"
        ).format(self._schema), (mapping_id, bar.bar_date, bar.ohlc_basis)).fetchone()
        if current is None:
            connection.execute(sql.SQL(
                "INSERT INTO {}.price_bars (asset_source_id, interval, bar_date, ohlc_basis, open, high, low, close, "
                "adjusted_close, adjusted_close_basis, volume, retrieved_at, source_convention, session_status, "
                "quality_status, run_id, last_seen_at, last_seen_run_id) "
                "VALUES (%s, '1d', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(self._schema), _bar_values(mapping_id, run_id, bar, last_seen=bar.retrieved_at, last_seen_run=run_id))
            counts.inserted += 1
            return

        existing_content = current[:7] + current[8:11]
        incoming_content = _bar_content(bar)
        last_seen = (current[12], current[13])
        observation = (bar.retrieved_at, run_id)
        if incoming_content != existing_content and bar.retrieved_at in (current[7], last_seen[0]):
            counts.rejected += 1
            counts.diagnostics.append(f"equal_timestamp_conflict:{bar.bar_date.isoformat()}")
            return
        if observation < last_seen:
            counts.stale += 1
            return
        if incoming_content == existing_content:
            if observation > last_seen:
                connection.execute(sql.SQL(
                    "UPDATE {}.price_bars SET last_seen_at = %s, last_seen_run_id = %s "
                    "WHERE asset_source_id = %s AND interval = '1d' AND bar_date = %s AND ohlc_basis = %s"
                ).format(self._schema), (bar.retrieved_at, run_id, mapping_id, bar.bar_date, bar.ohlc_basis))
            counts.unchanged += 1
            return
        connection.execute(sql.SQL(
            "UPDATE {}.price_bars SET open = %s, high = %s, low = %s, close = %s, adjusted_close = %s, "
            "adjusted_close_basis = %s, volume = %s, retrieved_at = %s, source_convention = %s, "
            "session_status = %s, quality_status = %s, run_id = %s, last_seen_at = %s, last_seen_run_id = %s "
            "WHERE asset_source_id = %s AND interval = '1d' AND bar_date = %s AND ohlc_basis = %s"
        ).format(self._schema), (
            bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.adjusted_close_basis, bar.volume,
            bar.retrieved_at, bar.source_convention, bar.session_status, bar.quality_status, run_id,
            bar.retrieved_at, run_id, mapping_id, bar.bar_date, bar.ohlc_basis,
        ))
        counts.corrected += 1

    def _fail_run_after_rollback(self, run_id: int, code: str, received_count: int) -> None:
        with self._connect() as connection:
            connection.execute(sql.SQL(
                "UPDATE {}.ingestion_runs SET status = 'failed', finished_at = clock_timestamp(), "
                "received_count = %s, error_code = %s WHERE id = %s AND status = 'running'"
            ).format(self._schema), (received_count, code, run_id))


def _bar_content(bar: PriceBar1d) -> tuple[object, ...]:
    return (bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.adjusted_close_basis,
            bar.volume, bar.source_convention, bar.session_status, bar.quality_status)


def _bar_values(mapping_id: int, run_id: int, bar: PriceBar1d, *, last_seen, last_seen_run: int) -> tuple[object, ...]:
    return (mapping_id, bar.bar_date, bar.ohlc_basis, bar.open, bar.high, bar.low, bar.close,
            bar.adjusted_close, bar.adjusted_close_basis, bar.volume, bar.retrieved_at,
            bar.source_convention, bar.session_status, bar.quality_status, run_id, last_seen, last_seen_run)


def _safe_error_code(code: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        raise ValueError("error code must be a short lowercase identifier")
    return code


def _is_verified_convention(asset_id: str, source_id: str, symbol: str, convention: str) -> bool:
    mapping = ASSET_SOURCES.get(asset_id)
    return (
        mapping is not None
        and mapping.source_convention.startswith("verified:")
        and (source_id, symbol, convention) == (mapping.source_id, mapping.symbol, mapping.source_convention)
    )
