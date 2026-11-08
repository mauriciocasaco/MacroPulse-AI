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


@dataclass(frozen=True)
class MigrationResult:
    schema: str
    applied_versions: tuple[str, ...]
    current_versions: tuple[str, ...]
    asset_count: int
    source_count: int
    mapping_count: int


def apply_migrations(
    dsn: str, *, schema: str = "market_data", expected_pending: tuple[str, ...] | None = None,
    expected_checksums: Mapping[str, str] | None = None,
    connect: Callable[[str], psycopg.Connection] | None = None,
) -> MigrationResult:
    """Apply checked-in migrations atomically, optionally requiring an exact pending set."""
    _validate_schema(schema)
    paths = tuple(_MIGRATIONS / name for name in _MARKET_DATA_MIGRATIONS)
    if not paths:
        raise RuntimeError("no Market Data migrations were found")

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
            else:
                connection.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(ledger))
                recorded = tuple(row[0] for row in connection.execute(
                    sql.SQL("SELECT version FROM {} ORDER BY version").format(ledger)
                ).fetchall())
                versions = tuple(path.name.split("_", 1)[0] for path in paths)
                current = tuple(version for version in versions if version not in expected_pending)
                pending = tuple(version for version in versions if version not in current)
                if recorded != current or pending != expected_pending:
                    raise RuntimeError("unexpected pending Market Data migrations")
            for path in paths:
                version = path.name.split("_", 1)[0]
                source = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
                if expected_checksums is not None and expected_checksums.get(version) != checksum:
                    raise RuntimeError(f"migration checksum does not match the approved SQL: {version}")
                previous = connection.execute(
                    sql.SQL("SELECT checksum_sha256 FROM {}.schema_migrations WHERE version = %s")
                    .format(sql.Identifier(schema)),
                    (version,),
                ).fetchone()
                if previous is not None:
                    if previous[0] != checksum:
                        raise RuntimeError(f"migration checksum changed after application: {version}")
                    continue
                statement = sql.SQL(source.replace("market_data", schema))
                connection.execute(statement)
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
