"""Guarded migration and ingestion for the pinned Supabase DEV database."""

from argparse import ArgumentParser
from datetime import date
from hashlib import sha256
import os
from pathlib import Path

import psycopg

from backend.market_data.dev_import import (
    DEV_PROJECT_REF,
    configured_dev_dsn,
    connect_dev,
    dev_dsn,
)

from .contracts import CATALOG

_DATABASE = "postgres"
_VERSION = "202611090001"
_TABLES = {
    "series", "capture", "observation_revision", "revision_capture", "schema_migrations",
}
_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / f"{_VERSION}_macro_data.sql"
)


class MacroDevError(RuntimeError):
    """The approved DEV operation could not be verified or completed."""


def migration_source() -> tuple[str, str]:
    source = _MIGRATION.read_text(encoding="utf-8")
    return source, sha256(source.encode("utf-8")).hexdigest()


def migration_state(connection: psycopg.Connection, checksum: str) -> str:
    connection.execute("SET LOCAL search_path = pg_catalog")
    database, same_role, standby, can_create = connection.execute(
        """
        SELECT current_database(), current_user = session_user,
               pg_is_in_recovery(), has_database_privilege(current_database(), 'CREATE')
        """
    ).fetchone()
    if database != _DATABASE or not same_role or standby:
        raise MacroDevError("unexpected DEV database or execution role")

    schema = connection.execute(
        "SELECT nspowner = current_user::regrole FROM pg_catalog.pg_namespace WHERE nspname = 'macro_data'"
    ).fetchone()
    if schema is None:
        if not can_create:
            raise MacroDevError("the DEV role cannot create the macro_data schema")
        return "pending"
    if not schema[0]:
        raise MacroDevError("the DEV role does not own the macro_data schema")

    tables = connection.execute(
        """
        SELECT relname, relowner = current_user::regrole, relrowsecurity FROM pg_catalog.pg_class
        WHERE relnamespace = pg_catalog.to_regnamespace('macro_data') AND relkind = 'r'
        """
    ).fetchall()
    if {name for name, _, _ in tables} != _TABLES or not all(
        owned and rls for _, owned, rls in tables
    ):
        raise MacroDevError("unregistered macro_data schema or unexpected table inventory, ownership or RLS")
    unsafe_role = connection.execute(
        """
        SELECT EXISTS (
            SELECT FROM pg_catalog.pg_roles AS role
            WHERE role.rolname IN ('anon', 'authenticated', 'service_role')
              AND (
                has_schema_privilege(role.oid, 'macro_data', 'USAGE, CREATE')
                OR EXISTS (
                    SELECT FROM pg_catalog.pg_class AS relation
                    WHERE relation.relnamespace = 'macro_data'::regnamespace AND relation.relkind = 'r'
                      AND has_table_privilege(
                        role.oid, relation.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
                      )
                )
              )
        )
        """
    ).fetchone()[0]
    if unsafe_role:
        raise MacroDevError("a public API role has unexpected access to macro_data")
    versions = connection.execute(
        "SELECT version, checksum_sha256 FROM macro_data.schema_migrations ORDER BY version"
    ).fetchall()
    if versions != [(_VERSION, checksum)]:
        raise MacroDevError("macro_data migration state does not match the reviewed SQL")
    return "ready"


def preflight(dsn: str, checksum: str) -> str:
    # connect_dev verifies the pinned host/role suffix and full TLS identity.
    with connect_dev(dsn) as connection:
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '15s'")
        return migration_state(connection, checksum)


def apply_migration(connection: psycopg.Connection, source: str, checksum: str) -> str:
    if sha256(source.encode("utf-8")).hexdigest() != checksum:
        raise MacroDevError("migration source does not match the reviewed checksum")
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '3s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SELECT pg_catalog.pg_advisory_xact_lock(20261109, 1)")
        if migration_state(connection, checksum) == "ready":
            return "already applied"
        connection.execute(source)
        connection.execute(
            "INSERT INTO macro_data.schema_migrations (version, checksum_sha256) VALUES (%s, %s)",
            (_VERSION, checksum),
        )
        if migration_state(connection, checksum) != "ready":
            raise MacroDevError("migration could not be verified")
    return "applied"


def _dsn() -> str:
    if "DATABASE_URL" in os.environ or "MARKETPULSE_DEV_PROJECT_REF" in os.environ:
        return dev_dsn(os.environ.get("MARKETPULSE_DEV_PROJECT_REF"), os.environ.get("DATABASE_URL"))
    return configured_dev_dsn()


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--import-history", action="store_true")
    parser.add_argument("--confirm-dev-write")
    parser.add_argument("--expect-sha256")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2019, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--indicator", choices=tuple(CATALOG), action="append")
    parser.add_argument("--deposit-anchor", action="store_true")
    parser.add_argument("--ecb-history", action="store_true")
    args = parser.parse_args(argv)
    writing = args.apply or args.import_history
    if writing and args.confirm_dev_write != DEV_PROJECT_REF:
        parser.error("DEV writes require the exact authorized project reference")
    if args.import_history and (args.end is None or args.start > args.end):
        parser.error("history import requires an explicit end date on or after the start")
    if (args.indicator or args.deposit_anchor or args.ecb_history) and not args.import_history:
        parser.error("indicator selection, the deposit anchor and ECB versions are import options")
    if args.deposit_anchor and (args.start != date(2019, 1, 1) or
            (args.indicator and "deposit-rate" not in args.indicator)):
        parser.error("the verified deposit anchor is for the January 2019 import window")
    if args.ecb_history and (
        args.deposit_anchor or len(args.indicator or ()) != 1
        or CATALOG[args.indicator[0]].provider != "ecb" or (args.end - args.start).days > 366
    ):
        parser.error("ECB versions require one ECB indicator and a window no longer than 366 days")

    try:
        source, checksum = migration_source()
        if writing and args.expect_sha256 != checksum:
            raise MacroDevError("DEV writes require the reviewed migration SHA-256")
        dsn = _dsn()
        state = preflight(dsn, checksum)
        print(f"DEV {DEV_PROJECT_REF} macro_data {_VERSION}: {state}; SHA-256 {checksum}")
        if args.status and state == "ready":
            report_coverage(dsn, checksum)
        if args.apply and state == "pending":
            with connect_dev(dsn) as connection:
                result = apply_migration(connection, source, checksum)
            print(f"macro_data migration {result}")
            if preflight(dsn, checksum) != "ready":
                raise MacroDevError("migration did not reach ready state")
        if args.import_history:
            if state != "ready":
                raise MacroDevError("apply the reviewed macro_data migration before importing")
            return _import_history(
                dsn, checksum, args.start, args.end, args.indicator, args.deposit_anchor, args.ecb_history,
            )
    except MacroDevError as error:
        print(f"macro_data DEV: {error}")
        return 1
    except Exception as error:
        # Driver and upstream exception messages can contain private connection data.
        print(f"macro_data DEV operation failed ({type(error).__name__})")
        return 1
    return 0


def _import_history(
    dsn: str, checksum: str, start: date, end: date, ids: list[str] | None, anchor: bool, ecb_history: bool,
) -> int:
    from .ingest import run_ingestion

    with connect_dev(dsn) as connection:
        with connection.transaction():
            if migration_state(connection, checksum) != "ready":
                raise MacroDevError("macro_data is not ready on the import connection")
        results = run_ingestion(connection, start, end, ids=ids, ecb_history=ecb_history)
        if anchor:
            # Fetch the last verified event before the standard import window as
            # its own observation, preserving its actual 2016 effective date.
            results += run_ingestion(
                connection, date(2016, 3, 16), date(2018, 12, 31), ids=("deposit-rate",),
            )
    for result in results:
        print(
            f"{result.indicator_id} {result.outcome}: capture={result.capture_id} "
            f"observations={result.observations} revisions={result.new_revisions} "
            f"stale={result.stale_observations} HTTP={result.http_status} "
            f"attempts={result.attempts} error={result.error_code or '-'}"
        )
    return int(
        any(result.outcome != "success" for result in results)
        or (anchor and results[-1].observations != 1)
    )


def report_coverage(dsn: str, checksum: str) -> None:
    from .repository import read_observations

    with connect_dev(dsn) as connection:
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '15s'")
        if migration_state(connection, checksum) != "ready":
            raise MacroDevError("macro_data is not ready on the status connection")
        for indicator in CATALOG.values():
            rows = read_observations(connection, indicator.id)
            numeric = [row for row in rows if row["value_decimal"] is not None]
            if not numeric:
                print(f"{indicator.id}: 0 numeric observations")
                continue
            print(
                f"{indicator.id}: {len(numeric)} numeric observations, "
                f"{len(rows) - len(numeric)} missing, "
                f"{numeric[0]['period_raw']} through {numeric[-1]['period_raw']}, "
                f"last={numeric[-1]['value_decimal']} ({indicator.unit}, scale {indicator.multiplier})"
            )


if __name__ == "__main__":
    raise SystemExit(main())
