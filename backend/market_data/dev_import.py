"""Fail-closed destination and preflight checks for the first Supabase DEV load."""

from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING

import psycopg
from psycopg import sql
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .catalog import ASSETS, ASSET_SOURCES
from .connection import secure_supabase_dsn
from .migrations import (
    NEWS_VERSIONS, _APPROVED_CHECKSUMS, _migration_sources, _validate_ledger,
    apply_migrations, news_vector_version,
)
from .repository import _bar_content

if TYPE_CHECKING:
    from .historical_import import PreparedExport


DEV_PROJECT_REF = "swpithkjviavycrxpwed"
INITIAL_VERSION = "202611070001"
INCREMENTAL_VERSION = "202611080001"
_NEWS_TABLES = {
    "sources", "articles", "article_records", "article_assets", "record_assets", "ingestion_batches",
    "embedding_models", "record_embeddings",
}

# PostgreSQL 17 catalog signatures from the audited initial SQL. The incremental
# changes the catalog rows only. A different physical schema requires review.
_SCHEMA_SIGNATURES = {
    "assets": "c83bb7d79c443e2176efeccdf05acc7d8d52c01ebde4406469e4e2dbe1e005e5",
    "data_sources": "01bb15c5f629de4839843e5d50ed5a4d316ab183391f7749ef1a1b75f0d103c6",
    "asset_sources": "afa9595eafd30dd82685a0000284631c9d8fde0cea67863e6ec9dea0cf31274d",
    "ingestion_runs": "acf20b7d59f12270d2771af4af60aed2327228cf7e05264ddbaaafb569375d79",
    "price_bars": "2643895eb1ae20aeff9cc38f66d15aaa2371fa29a4a10be3b8c4f732694473aa",
}


class DevImportError(RuntimeError):
    """A destination or preflight guard rejected a DEV operation."""


@dataclass(frozen=True)
class DevPreflight:
    versions: tuple[str, ...]
    bars_by_symbol: dict[str, int]
    runs_by_status: dict[str, int]
    unresolved_runs: tuple[tuple[int, str, str, str | None], ...]


@dataclass(frozen=True)
class DevNewsPreflight:
    versions: tuple[str, ...]
    pending: tuple[str, ...]
    vector_version: str


def _endpoint_matches(host: str, port: str, user: str) -> bool:
    role = r"[A-Za-z_][A-Za-z0-9_]*"
    return bool(
        (host == f"db.{DEV_PROJECT_REF}.supabase.co" and port == "5432" and re.fullmatch(role, user))
        or (re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host) and port in ("5432", "6543")
            and re.fullmatch(rf"{role}\.{DEV_PROJECT_REF}", user))
    )


def dev_dsn(project_ref: str | None, dsn: str | None) -> str:
    if project_ref != DEV_PROJECT_REF:
        raise DevImportError("MARKETPULSE_DEV_PROJECT_REF must identify the authorized DEV project")
    if not dsn:
        raise DevImportError("DATABASE_URL is missing")
    try:
        params = conninfo_to_dict(dsn)
    except (ValueError, psycopg.Error):
        raise DevImportError("invalid PostgreSQL DATABASE_URL") from None
    required = {"host", "port", "dbname", "user", "password"}
    if any(not params.get(key) for key in required):
        raise DevImportError("DATABASE_URL requires an explicit host, port, database, user and password")
    if (
        params.keys() - required - {"sslmode"}
        or params.get("sslmode", "verify-full") not in ("require", "verify-full")
        or params["dbname"] != "postgres"
        or not _endpoint_matches(params["host"], params["port"], params["user"])
    ):
        raise DevImportError("DATABASE_URL has an unapproved DEV endpoint, routing option or TLS mode")
    secured = secure_supabase_dsn(make_conninfo(**params, connect_timeout=8))
    if not Path(conninfo_to_dict(secured)["sslrootcert"]).is_file():
        raise DevImportError("configured Supabase CA certificate is missing")
    return secured


def configured_dev_dsn() -> str:
    """Read the existing local configuration only for explicit DEV modes."""
    try:
        values = dotenv_values(".env", encoding="utf-8-sig", interpolate=False)
    except (OSError, ValueError):
        raise DevImportError("local DEV configuration could not be read") from None
    return dev_dsn(values.get("MARKETPULSE_DEV_PROJECT_REF"), values.get("DATABASE_URL"))


def connect_dev(dsn: str) -> psycopg.Connection:
    """Check the approved route and strict TLS for every DEV connection."""
    params = conninfo_to_dict(dsn)
    if (
        params.get("sslmode") != "verify-full"
        or params.get("gssencmode") != "disable"
        or params.get("sslrootcert") != conninfo_to_dict(secure_supabase_dsn(""))["sslrootcert"]
        or params.keys() - {"host", "port", "dbname", "user", "password", "sslmode", "sslrootcert",
                            "gssencmode", "connect_timeout"}
        or params.get("dbname") != "postgres"
        or not _endpoint_matches(params.get("host", ""), params.get("port", ""), params.get("user", ""))
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS"))
    ):
        raise DevImportError("strict TLS and an unambiguous DEV route are required")
    # Supavisor transaction pooling does not support server-side prepared statements.
    connection = psycopg.connect(dsn, prepare_threshold=None)
    if (
        not connection.pgconn.ssl_in_use
        or connection.info.host != params["host"]
        or str(connection.info.port) != params["port"]
    ):
        connection.close()
        raise DevImportError("the verified TLS connection does not match the approved endpoint")
    return connection


def _fingerprint(value: object, *, sorted_keys: bool = False) -> str:
    data = json.dumps(value, separators=(",", ":"), sort_keys=sorted_keys, default=str)
    return sha256(data.encode("utf-8")).hexdigest()


_CATALOG_QUERIES = {
    "columns": """
        SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
               a.attidentity, a.attgenerated, pg_get_expr(d.adbin,d.adrelid)
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON (d.adrelid,d.adnum)=(a.attrelid,a.attnum)
        WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
    """,
    "constraints": """
        SELECT conname, contype, convalidated, condeferrable, condeferred, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname
    """,
    "indexes": """
        SELECT cls.relname, idx.indisvalid, idx.indisready, pg_get_indexdef(idx.indexrelid)
        FROM pg_index idx JOIN pg_class cls ON cls.oid=idx.indexrelid
        WHERE idx.indrelid=to_regclass(%s) ORDER BY cls.relname
    """,
    "triggers": """
        SELECT tgname, tgenabled, pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid)
        FROM pg_trigger t WHERE tgrelid=to_regclass(%s) AND NOT tgisinternal ORDER BY tgname
    """,
}


def _check_schema(connection: psycopg.Connection) -> None:
    # Canonical deparsing and lookup independent of the database role's search path.
    connection.execute("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)")
    for table, expected in _SCHEMA_SIGNATURES.items():
        args = (f"market_data.{table}",)
        kind = connection.execute(
            "SELECT relkind, relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=to_regclass(%s)", args,
        ).fetchone()
        if kind is None:
            raise DevImportError(f"missing market_data.{table}")
        shape = {"kind": kind[0], "rls": kind[1:]}
        for category, query in _CATALOG_QUERIES.items():
            rows = connection.execute(query, args).fetchall()
            shape[category] = (len(rows), _fingerprint(rows))
        if _fingerprint(shape, sorted_keys=True) != expected:
            raise DevImportError(f"incompatible DDL for market_data.{table}")


def _check_ledger(connection: psycopg.Connection, *, pending_incremental: bool) -> tuple[str, ...]:
    relation = connection.execute(
        "SELECT relkind, relrowsecurity FROM pg_class WHERE oid=to_regclass('market_data.schema_migrations')"
    ).fetchone()
    if relation != ("r", False):
        raise DevImportError("migration ledger is missing or incompatible")
    try:
        versions = _validate_ledger(connection, sql.Identifier("market_data", "schema_migrations"), _migration_sources())
    except RuntimeError as error:
        raise DevImportError("required migration versions or checksums do not match") from error
    valid = versions == (INITIAL_VERSION,) if pending_incremental else versions[:2] == (INITIAL_VERSION, INCREMENTAL_VERSION)
    if not valid:
        raise DevImportError("required migration versions or checksums do not match")
    return versions


def _check_catalog(connection: psycopg.Connection, *, pending_incremental: bool) -> None:
    assets = connection.execute(
        "SELECT id, name, category, instrument_type, quote_currency, economic_unit, calendar_code, sign_policy "
        "FROM market_data.assets"
    ).fetchall()
    expected_assets = {
        (a.id, a.name, a.category, a.instrument_type, a.quote_currency, a.economic_unit, a.calendar_code, a.sign_policy)
        for a in ASSETS.values()
    }
    providers = connection.execute("SELECT id, name FROM market_data.data_sources").fetchall()
    mappings = connection.execute(
        "SELECT asset_id, source_id, symbol, source_convention, rolling_method, volume_unit FROM market_data.asset_sources"
    ).fetchall()
    expected_mappings = {
        (m.asset_id, m.source_id, m.symbol,
         "unknown" if pending_incremental and m.asset_id != "bitcoin_usd" else m.source_convention,
         m.rolling_method, m.volume_unit)
        for m in ASSET_SOURCES.values()
    }
    if set(assets) != expected_assets or providers != [("yahoo_finance", "Yahoo Finance")] or set(mappings) != expected_mappings:
        raise DevImportError("DEV catalog identities, conventions or financial policies do not match")


def _check_existing(
    connection: psycopg.Connection, exports: tuple[PreparedExport, ...], *, require_complete: bool,
) -> tuple[dict[str, int], dict[str, int], tuple[tuple[int, str, str, str | None], ...]]:
    expected = {}
    archives = {}
    counts = dict.fromkeys((export.mapping.symbol for export in exports), 0)
    for export in exports:
        asset = export.mapping.asset_id
        for prepared in export.batches:
            window = (asset, prepared.window.start, prepared.window.end_exclusive)
            archive_marker = f"warning:archive:{prepared.raw_file}:sha256:{prepared.raw_sha256}"
            archives[window] = archive_marker
            for bar in prepared.batch.bars:
                expected[asset, bar.interval, bar.bar_date, bar.ohlc_basis] = (
                    _bar_content(bar), bar.retrieved_at, bar.adapter_version, bar.provider_version, archive_marker,
                )

    runs = Counter()
    unresolved = []
    for run_id, asset, symbol, start, end, status, error_code, archive_matches in connection.execute("""
        SELECT r.id, m.asset_id, m.symbol, r.start_date, r.end_date_exclusive, r.status, r.error_code,
               ARRAY(SELECT item FROM jsonb_array_elements_text(r.diagnostics) AS item
                     WHERE item LIKE 'warning:archive:%')
        FROM market_data.ingestion_runs r JOIN market_data.asset_sources m ON m.id=r.asset_source_id
        ORDER BY r.id
    """):
        marker = archives.get((asset, start, end))
        if marker is None or (status in ("partial", "succeeded") and archive_matches != [marker]):
            raise DevImportError("existing ingestion runs do not match the planned archives")
        runs[status] += 1
        if status in ("running", "failed", "abandoned"):
            safe_code = error_code if error_code in (None, "persistence_failed", "import_interrupted") else "other"
            unresolved.append((run_id, status, symbol, safe_code))

    query = """
        SELECT m.asset_id, m.symbol, b.interval, b.bar_date, b.ohlc_basis,
               b.open, b.high, b.low, b.close, b.adjusted_close, b.adjusted_close_basis, b.volume,
               b.source_convention, b.session_status, b.quality_status,
               b.retrieved_at, b.last_seen_at, r.adapter_version, r.provider_version, r.diagnostics
        FROM market_data.price_bars b
        JOIN market_data.asset_sources m ON m.id=b.asset_source_id
        JOIN market_data.ingestion_runs r ON r.id=b.run_id
    """
    for asset, symbol, interval, day, basis, *values in connection.execute(query):
        candidate = expected.get((asset, interval, day, basis))
        content, retrieved, last_seen, adapter, provider, diagnostics = tuple(values[:10]), *values[10:]
        if (
            candidate is None or candidate[:4] != (content, retrieved, adapter, provider)
            or last_seen != retrieved or candidate[4] not in diagnostics
        ):
            raise DevImportError(f"unexpected overlap or provenance for {symbol} on {day}")
        counts[symbol] += 1
    if require_complete and sum(counts.values()) != len(expected):
        raise DevImportError("stored bar counts do not match the complete initial dataset")
    return counts, dict(runs), tuple(unresolved)


def preflight_dev(
    dsn: str, exports: tuple[PreparedExport, ...], *, pending_incremental: bool = False,
    require_complete: bool = False,
) -> DevPreflight:
    """Inspect DEV using a repeatable, read-only snapshot before any persistence."""
    if not exports:
        raise DevImportError("DEV preflight requires verified archives")
    try:
        with connect_dev(dsn) as connection:
            _start_read_only_preflight(connection)
            _check_schema(connection)
            versions = _check_ledger(connection, pending_incremental=pending_incremental)
            _check_catalog(connection, pending_incremental=pending_incremental)
            counts, runs, unresolved = _check_existing(connection, exports, require_complete=require_complete)
            if pending_incremental and (sum(counts.values()) or runs):
                raise DevImportError("the initial destination is no longer empty")
            return DevPreflight(versions, counts, runs, unresolved)
    except (psycopg.Error, OSError, ValueError) as error:
        raise DevImportError(f"DEV preflight failed ({type(error).__name__})") from None


def _start_read_only_preflight(connection: psycopg.Connection) -> None:
    connection.read_only = True
    connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    state = connection.execute(
        "SELECT pg_catalog.current_setting('transaction_read_only'), "
        "pg_catalog.current_setting('transaction_isolation'), "
        "pg_catalog.current_setting('server_version_num')::integer"
    ).fetchone()
    if state[:2] != ("on", "repeatable read") or state[2] // 10000 != 17:
        raise DevImportError("preflight requires a repeatable read-only PostgreSQL 17 transaction")


def preflight_dev_news(dsn: str) -> DevNewsPreflight:
    """Check readiness for the news batch or the completed news schema."""
    try:
        with connect_dev(dsn) as connection:
            _start_read_only_preflight(connection)
            _check_schema(connection)
            versions = _check_ledger(connection, pending_incremental=False)
            _check_catalog(connection, pending_incremental=False)
            vector_version = news_vector_version(connection)
            pending = tuple(version for version in NEWS_VERSIONS if version not in versions)
            if pending == NEWS_VERSIONS:
                if connection.execute("SELECT pg_catalog.to_regnamespace('news') IS NOT NULL").fetchone()[0]:
                    raise DevImportError("unregistered news schema already exists")
            elif pending:
                raise DevImportError("news ledger contains a partial batch")
            else:
                tables = connection.execute("""
                    SELECT relname FROM pg_catalog.pg_class
                    WHERE relnamespace = pg_catalog.to_regnamespace('news') AND relkind = 'r'
                """).fetchall()
                if not _NEWS_TABLES.issubset(name for name, in tables):
                    raise DevImportError("registered news schema is missing required tables")
            return DevNewsPreflight(versions, pending, vector_version)
    except DevImportError:
        raise
    except (psycopg.Error, OSError, ValueError, RuntimeError) as error:
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        raise DevImportError(f"DEV news preflight failed ({detail})") from None


def migrate_dev_news(dsn: str, *, confirm_project: str) -> tuple[str, ...]:
    if confirm_project != DEV_PROJECT_REF:
        raise DevImportError("DEV news writes require confirmation of the authorized project")
    if preflight_dev_news(dsn).pending != NEWS_VERSIONS:
        raise DevImportError("DEV news migration requires all three versions to be pending")
    try:
        result = apply_migrations(
            dsn, expected_pending=NEWS_VERSIONS, expected_checksums=_APPROVED_CHECKSUMS, connect=connect_dev,
        )
        return result.applied_versions
    except (psycopg.Error, OSError, ValueError, RuntimeError) as error:
        state = getattr(error, "sqlstate", None)
        detail = f"{type(error).__name__}, SQLSTATE {state}" if state else type(error).__name__
        raise DevImportError(f"DEV news migration failed ({detail})") from None


def migrate_dev_incremental(dsn: str, exports: tuple[PreparedExport, ...]) -> None:
    preflight_dev(dsn, exports, pending_incremental=True)
    try:
        apply_migrations(
            dsn, expected_pending=(INCREMENTAL_VERSION,), expected_checksums=_APPROVED_CHECKSUMS,
            connect=connect_dev,
        )
    except (psycopg.Error, OSError, ValueError, RuntimeError) as error:
        raise DevImportError(f"DEV incremental migration failed ({type(error).__name__})") from None


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Inspect or apply the guarded Supabase DEV news migrations.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-news", action="store_true")
    mode.add_argument("--apply-news", action="store_true")
    parser.add_argument("--confirm-dev-write", metavar="PROJECT_REF")
    args = parser.parse_args(argv)
    if args.apply_news and args.confirm_dev_write != DEV_PROJECT_REF:
        parser.error("DEV news writes require --confirm-dev-write with the authorized project reference")
    if args.preflight_news and args.confirm_dev_write is not None:
        parser.error("--confirm-dev-write requires --apply-news")
    try:
        dsn = configured_dev_dsn()
        if args.apply_news:
            print("DEV NEWS APPLIED " + ", ".join(migrate_dev_news(dsn, confirm_project=args.confirm_dev_write)))
        else:
            result = preflight_dev_news(dsn)
            state = "pending " + ", ".join(result.pending) if result.pending else "ready"
            print(f"DEV NEWS PREFLIGHT PASS  pgvector {result.vector_version} in extensions  {state}")
    except DevImportError as error:
        print(f"ERROR  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
