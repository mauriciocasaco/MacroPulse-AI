"""Versioned PostgreSQL migrations for the Market Data schema."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import psycopg
from psycopg import sql

_SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MARKET_DATA_MIGRATIONS = (
    "202611070001_market_data.sql",
    "202611080001_yahoo_daily_conventions.sql",
)
_NEWS_MIGRATIONS = (
    "202611080002_news.sql",
    "202611080003_news_ingestion.sql",
    "202611080004_news_embeddings.sql",
)
NEWS_VERSIONS = tuple(name.split("_", 1)[0] for name in _NEWS_MIGRATIONS)
_APPROVED_CHECKSUMS = {
    "202611070001": "de4a187debce6a195a88177ac255d2d2fe1e22b3647620bbd152d0687d29f43d",
    "202611080001": "a02db81979269c5c5663c4faaf3ad9c71862a2e2e703d5c2b5ed55c49054f394",
    "202611080002": "aa819c3556077439be0bb10b6e7a2607da8363306ed2cb331d306572a4f252c6",
    "202611080003": "d91f461c60b63a5e29afc9b120ab836e40247d57b75b65a2e5a14f22e4183f43",
    "202611080004": "11ce64cfe9514889272742f23f9795492cb64bc22612330e8a0efc3836a3e0dc",
}


@dataclass(frozen=True)
class MigrationResult:
    schema: str
    applied_versions: tuple[str, ...]
    current_versions: tuple[str, ...]
    asset_count: int
    source_count: int
    mapping_count: int


def _migration_sources() -> dict[str, tuple[str, str]]:
    sources = {}
    for name in (*_MARKET_DATA_MIGRATIONS, *_NEWS_MIGRATIONS):
        version = name.split("_", 1)[0]
        source = (_MIGRATIONS / name).read_text(encoding="utf-8")
        checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if checksum != _APPROVED_CHECKSUMS.get(version):
            raise RuntimeError(f"migration checksum does not match the approved SQL: {version}")
        sources[version] = (source, checksum)
    return sources


def _validate_ledger(connection, ledger, sources) -> tuple[str, ...]:
    rows = connection.execute(
        sql.SQL("SELECT version, checksum_sha256 FROM {} ORDER BY version").format(ledger)
    ).fetchall()
    for version, checksum in rows:
        if version not in sources:
            raise RuntimeError(f"unknown migration version: {version}")
        if checksum != sources[version][1]:
            raise RuntimeError(f"migration checksum changed after application: {version}")
    recorded = tuple(version for version, _ in rows)
    if recorded != tuple(sources)[:len(recorded)]:
        raise RuntimeError("migration ledger is not an ordered prefix of the registry")
    return recorded


def news_vector_version(connection: psycopg.Connection) -> str:
    version = connection.execute("""
        SELECT e.extversion
        FROM pg_catalog.pg_extension e
        JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'vector' AND n.nspname = 'extensions'
          AND pg_catalog.to_regtype('extensions.vector') IS NOT NULL
          AND pg_catalog.to_regprocedure('extensions.vector_dims(extensions.vector)') IS NOT NULL
          AND pg_catalog.to_regprocedure('extensions.vector_norm(extensions.vector)') IS NOT NULL
          AND pg_catalog.to_regoperator('extensions.<=>(extensions.vector,extensions.vector)') IS NOT NULL
    """).fetchone()
    if version is None:
        raise RuntimeError("news requires compatible pgvector already installed in extensions")
    return version[0]


def apply_migrations(
    dsn: str, *, schema: str = "market_data", expected_pending: tuple[str, ...] | None = None,
    expected_checksums: Mapping[str, str] | None = None,
    connect: Callable[[str], psycopg.Connection] | None = None,
) -> MigrationResult:
    """Apply market migrations by default. News requires its exact pending batch."""
    _validate_schema(schema)
    apply_news = expected_pending == NEWS_VERSIONS
    if apply_news and schema != "market_data":
        raise ValueError("news migrations require the market_data schema")
    sources = _migration_sources()
    market_versions = tuple(name.split("_", 1)[0] for name in _MARKET_DATA_MIGRATIONS)
    versions = tuple(sources) if apply_news else market_versions
    if expected_checksums is not None:
        for version in versions:
            if sources[version][1] != expected_checksums.get(version):
                raise RuntimeError(f"migration checksum does not match the approved SQL: {version}")

    applied: list[str] = []
    with (connect or psycopg.connect)(dsn) as connection:
        with connection.transaction():
            ledger = sql.Identifier(schema, "schema_migrations")
            if expected_pending is None:
                connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} ").format(sql.Identifier(schema)))
                connection.execute(sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} (version text PRIMARY KEY, checksum_sha256 text NOT NULL, "
                    "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"
                ).format(ledger))
            if apply_news:
                connection.execute("SET LOCAL search_path = pg_catalog")
                connection.execute("SET LOCAL lock_timeout = '3s'")
                connection.execute("SET LOCAL statement_timeout = '30s'")
            connection.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(ledger))
            recorded = _validate_ledger(connection, ledger, sources)
            pending = tuple(version for version in versions if version not in recorded)
            if expected_pending is not None and (
                pending != expected_pending or recorded != versions[:len(versions) - len(pending)]
            ):
                raise RuntimeError("unexpected pending migrations")
            if apply_news:
                news_vector_version(connection)
                if connection.execute("SELECT pg_catalog.to_regnamespace('news') IS NOT NULL").fetchone()[0]:
                    raise RuntimeError("news schema already exists")
            for version in pending:
                source, checksum = sources[version]
                connection.execute(source if schema == "market_data" else source.replace("market_data", schema))
                connection.execute(
                    sql.SQL("INSERT INTO {}.schema_migrations (version, checksum_sha256) VALUES (%s, %s)")
                    .format(sql.Identifier(schema)),
                    (version, checksum),
                )
                applied.append(version)
            return inspect_schema(connection, schema=schema, applied_versions=tuple(applied))


def inspect_schema(connection: psycopg.Connection, *, schema: str = "market_data", applied_versions: tuple[str, ...] = ()) -> MigrationResult:
    _validate_schema(schema)
    tables = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE'",
        (schema,),
    ).fetchall()
    table_names = {row[0] for row in tables}
    required = {"assets", "data_sources", "asset_sources", "ingestion_runs", "price_bars", "schema_migrations"}
    if not required.issubset(table_names):
        missing = sorted(required - table_names)
        raise RuntimeError(f"Market Data schema is missing required tables: {', '.join(missing)}")
    versions = tuple(row[0] for row in connection.execute(
        sql.SQL("SELECT version FROM {}.schema_migrations ORDER BY version").format(sql.Identifier(schema))
    ).fetchall())
    counts = connection.execute(sql.SQL(
        "SELECT (SELECT count(*) FROM {}.assets), "
        "(SELECT count(*) FROM {}.data_sources), "
        "(SELECT count(*) FROM {}.asset_sources)"
    ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))).fetchone()
    return MigrationResult(schema, applied_versions, versions, *counts)


def _validate_schema(schema: str) -> None:
    if not _SCHEMA_NAME.fullmatch(schema):
        raise ValueError("schema must be a lowercase PostgreSQL identifier")
