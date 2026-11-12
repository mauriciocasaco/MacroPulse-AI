"""Read and verify the sanitized DEMO macro projection."""

from __future__ import annotations

from datetime import date, timedelta, timezone

from fastapi import HTTPException
import psycopg
from psycopg.rows import tuple_row

from .api import MacroHistory, MacroObservation, MacroWindow, indicator_metadata
from .contracts import CATALOG, Indicator


_ALLOWED_RELATIONS = {
    ("marketpulse_demo", "macro_catalog", "r"),
    ("marketpulse_demo", "macro_observations", "r"),
}
_PROJECTION_COLUMNS = {
    "macro_catalog": {
        "indicator_id", "series_key", "captured_at", "deposit_coverage", "requested_start",
        "requested_end", "response_sha256", "bundle_sha256", "projected_rows", "projection_sha256",
    },
    "macro_observations": {
        "indicator_id", "period_raw", "period_start", "period_end", "effective_date",
        "value_decimal", "status_kind", "status_raw",
    },
}


# Fixed array field order, primary-key order, ISO dates and UTC timestamps
# keep the digest independent of session settings. The limit bounds the
# aggregate even if a privileged write bloats the projection.
_INTEGRITY_ROWS = """
    SELECT c.indicator_id, c.projected_rows, c.projection_sha256, projected.row_count,
           encode(sha256(convert_to(jsonb_build_array(
               c.indicator_id, c.series_key,
               to_char(c.captured_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
               to_char(c.requested_start, 'YYYY-MM-DD'), to_char(c.requested_end, 'YYYY-MM-DD'),
               c.response_sha256, c.bundle_sha256,
               (SELECT jsonb_agg(jsonb_build_array(
                   to_char(lower(span), 'YYYY-MM-DD'), to_char(upper(span), 'YYYY-MM-DD')
               ) ORDER BY lower(span)) FROM unnest(c.deposit_coverage) AS coverage(span)),
               projected.rows
           )::text, 'UTF8')), 'hex')
    FROM ONLY marketpulse_demo.macro_catalog AS c
    CROSS JOIN LATERAL (
        SELECT count(*) AS row_count, jsonb_agg(jsonb_build_array(
            o.period_raw, to_char(o.period_start, 'YYYY-MM-DD'), to_char(o.period_end, 'YYYY-MM-DD'),
            to_char(o.effective_date, 'YYYY-MM-DD'), o.value_decimal::text, o.status_kind, o.status_raw
        ) ORDER BY o.period_start) AS rows
        FROM (
            SELECT period_raw, period_start, period_end, effective_date, value_decimal, status_kind, status_raw
            FROM ONLY marketpulse_demo.macro_observations
            WHERE indicator_id = c.indicator_id
            ORDER BY period_start LIMIT 5501
        ) AS o
    ) AS projected
"""


class ProjectionError(RuntimeError):
    """A fixed, non-sensitive DEMO projection validation failure."""


def _integrity_rows(connection: psycopg.Connection):
    with connection.cursor(row_factory=tuple_row) as cursor:
        rows = cursor.execute(_INTEGRITY_ROWS).fetchall()
    if {indicator for indicator, *_ in rows} != set(CATALOG):
        raise ProjectionError("Public demo macro projection is incomplete")
    return rows


def verify_projection_integrity(connection: psycopg.Connection) -> None:
    for _, stored_count, stored_digest, live_count, live_digest in _integrity_rows(connection):
        if (stored_count, stored_digest) != (live_count, live_digest):
            raise ProjectionError("Public demo macro projection integrity check failed")


def stamp_projection(connection: psycopg.Connection) -> None:
    """Bind a newly copied projection to its exact values and coverage."""
    hashes = []
    for indicator, expected_count, _, live_count, live_digest in _integrity_rows(connection):
        if live_count != expected_count:
            raise ProjectionError("Public demo macro projection row count differs from the bundle")
        hashes.append((live_digest, indicator))
    with connection.cursor() as cursor:
        cursor.executemany("""
            UPDATE ONLY marketpulse_demo.macro_catalog SET projection_sha256 = %s WHERE indicator_id = %s
        """, hashes)


_SHARED_SCOPE_CHECK = """
    WITH role_and_database AS (
        SELECT r.oid AS role_id, d.oid AS database_id
        FROM pg_roles r CROSS JOIN pg_database d
        WHERE r.rolname = current_user AND d.datname = current_database()
    ), system_schemas AS (
        SELECT oid, nspname FROM pg_namespace WHERE nspname IN ('pg_catalog', 'information_schema')
    ), system_acls AS (
        SELECT 'pg_proc'::regclass AS class_id, p.oid AS object_id, 0 AS column_id,
            COALESCE(p.proacl, acldefault('f', p.proowner)) AS acl,
            acldefault('f', p.proowner) AS default_acl, false AS metadata_select
        FROM pg_proc p JOIN system_schemas n ON n.oid = p.pronamespace
        WHERE p.proacl IS NOT NULL OR p.oid >= 16384
        UNION ALL
        SELECT 'pg_class'::regclass, c.oid, 0, c.relacl, NULL::aclitem[], n.nspname = 'information_schema'
        FROM pg_class c JOIN system_schemas n ON n.oid = c.relnamespace WHERE c.relacl IS NOT NULL
        UNION ALL
        SELECT 'pg_class'::regclass, c.oid, a.attnum, a.attacl, NULL::aclitem[], false
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN system_schemas n ON n.oid = c.relnamespace WHERE a.attacl IS NOT NULL
    )
    SELECT NOT has_database_privilege(current_database(), 'CONNECT WITH GRANT OPTION')
        AND NOT has_schema_privilege('marketpulse_demo', 'USAGE WITH GRANT OPTION')
        AND NOT EXISTS (
            SELECT 1 FROM pg_namespace n
            WHERE has_schema_privilege(n.oid, 'CREATE') OR (
                n.oid NOT IN (SELECT oid FROM system_schemas) AND n.nspname <> 'marketpulse_demo'
                AND has_schema_privilege(n.oid, 'USAGE')
            )
        )
        AND NOT EXISTS (
            SELECT 1 FROM pg_class c
            WHERE has_schema_privilege(c.relnamespace, 'USAGE') AND CASE WHEN c.relkind = 'S'
                THEN has_sequence_privilege(c.oid, 'SELECT, USAGE, UPDATE') ELSE false END
        )
        AND NOT EXISTS (
            SELECT 1 FROM pg_parameter_acl p
            CROSS JOIN LATERAL aclexplode(p.paracl) a
            WHERE a.grantee IN (0, r.role_id) AND a.privilege_type IN ('SET', 'ALTER SYSTEM')
        )
        AND NOT EXISTS (
            -- Direct ACL dependencies also reveal dormant grants in inaccessible schemas.
            SELECT 1 FROM pg_shdepend s
            WHERE s.refclassid = 'pg_authid'::regclass AND s.refobjid = r.role_id
                AND s.dbid IN (0, r.database_id) AND s.deptype IN ('a', 'o')
                AND NOT (s.deptype = 'a' AND s.objsubid = 0 AND (
                    (s.classid = 'pg_database'::regclass AND s.objid = r.database_id)
                    OR (s.classid = 'pg_namespace'::regclass AND s.objid = 'marketpulse_demo'::regnamespace)
                    OR (s.classid = 'pg_class'::regclass AND s.objid IN (
                        'marketpulse_demo.macro_catalog'::regclass,
                        'marketpulse_demo.macro_observations'::regclass
                    ))
                ))
        )
        AND NOT EXISTS (
            -- Ordinary objects allocated after initdb start at OID 16384.
            -- Keep bootstrap catalog access, but reject custom or expanded PUBLIC access.
            SELECT 1 FROM system_acls s
            LEFT JOIN pg_init_privs i ON i.classoid = s.class_id AND i.objoid = s.object_id
                AND i.objsubid = s.column_id AND i.privtype = 'i'
            CROSS JOIN LATERAL aclexplode(s.acl) a
            WHERE a.grantee = 0 AND (s.object_id >= 16384 OR (
                -- initdb does not record the information_schema metadata SELECT ACLs in pg_init_privs.
                NOT (s.metadata_select AND a.privilege_type = 'SELECT') AND NOT EXISTS (
                    SELECT 1 FROM aclexplode(COALESCE(i.initprivs, s.default_acl)) original
                    WHERE original.grantee = 0 AND original.privilege_type = a.privilege_type
                )
            ))
        )
        AND NOT EXISTS (SELECT 1 FROM pg_foreign_server WHERE has_server_privilege(oid, 'USAGE'))
        AND NOT EXISTS (SELECT 1 FROM pg_foreign_data_wrapper WHERE has_foreign_data_wrapper_privilege(oid, 'USAGE'))
        AND NOT EXISTS (
            SELECT 1 FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace
                AND p.proname IN ('lo_creat', 'lo_create', 'lo_from_bytea', 'pg_logical_emit_message')
                AND has_function_privilege(p.oid, 'EXECUTE')
        )
        AND current_setting('lo_compat_privileges') = 'off'
        AND NOT EXISTS (
            SELECT 1 FROM pg_largeobject_metadata lo
            WHERE lo.lomowner = r.role_id OR EXISTS (
                SELECT 1 FROM aclexplode(lo.lomacl) a WHERE a.grantee IN (0, r.role_id)
            )
        )
    FROM role_and_database r
"""


def verify_projection(connection: psycopg.Connection, *, shared_project: bool = False) -> None:
    with connection.cursor(row_factory=tuple_row) as cursor:
        permitted = cursor.execute("""
            SELECT (%s OR (to_regnamespace('macro_data') IS NULL
                AND to_regnamespace('market_data') IS NULL
                AND to_regnamespace('news') IS NULL))
                AND has_table_privilege(to_regclass('marketpulse_demo.macro_catalog'), 'SELECT')
                AND has_table_privilege(to_regclass('marketpulse_demo.macro_observations'), 'SELECT')
        """, (shared_project,)).fetchone()
        if permitted != (True,):
            raise ProjectionError("Public demo macro projection is unavailable")
        if shared_project and cursor.execute(_SHARED_SCOPE_CHECK).fetchone() != (True,):
            raise ProjectionError("Public demo macro role has unexpected shared database privileges")
        readable = cursor.execute("""
            SELECT n.nspname, c.relname, c.relkind
            FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname !~ '^pg_'
              AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND has_schema_privilege(n.oid, 'USAGE')
              AND has_any_column_privilege(c.oid, 'SELECT')
        """).fetchall()
        if set(readable) != _ALLOWED_RELATIONS:
            raise ProjectionError("Public demo macro role has an unexpected read scope")
        columns = cursor.execute("""
            SELECT c.relname, a.attname
            FROM pg_attribute AS a JOIN pg_class AS c ON c.oid = a.attrelid
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'marketpulse_demo' AND c.relname IN ('macro_catalog', 'macro_observations')
              AND a.attnum > 0 AND NOT a.attisdropped
        """).fetchall()
        if set(columns) != {(table, name) for table, names in _PROJECTION_COLUMNS.items() for name in names}:
            raise ProjectionError("Public demo macro projection has unexpected columns")
        catalog = cursor.execute("""
            SELECT c.indicator_id, c.series_key, EXISTS (
                SELECT 1 FROM ONLY marketpulse_demo.macro_observations AS o
                WHERE o.indicator_id = c.indicator_id AND o.value_decimal IS NOT NULL
            ), c.deposit_coverage IS NOT NULL
            FROM ONLY marketpulse_demo.macro_catalog AS c
        """).fetchall()
        expected = {(item.id, item.series_key, True, item.frequency == "effective_date") for item in CATALOG.values()}
        provenance = cursor.execute("""
            SELECT count(DISTINCT bundle_sha256) = 1
              AND count(DISTINCT (requested_start, requested_end)) = 1
              AND bool_and(captured_at <= statement_timestamp())
            FROM ONLY marketpulse_demo.macro_catalog
        """).fetchone()
        if set(catalog) != expected or provenance != (True,):
            raise ProjectionError("Public demo macro projection is incomplete")
    verify_projection_integrity(connection)


def read_history(
    connection: psycopg.Connection, item: Indicator, window: MacroWindow, *, max_points: int,
) -> MacroHistory:
    meta = connection.execute("""
        SELECT captured_at, (captured_at AT TIME ZONE 'Europe/Berlin')::date AS captured_on,
               (statement_timestamp() AT TIME ZONE 'Europe/Berlin')::date AS today
        FROM ONLY marketpulse_demo.macro_catalog WHERE indicator_id = %s
    """, (item.id,)).fetchone()
    if meta is None:
        raise ProjectionError("Public demo macro projection is unavailable")
    end = window.end
    anchor = coverage_start = coverage_end = None
    if item.frequency == "effective_date":
        end = min(end, meta["captured_on"], meta["today"])
        span = connection.execute("""
            SELECT lower(span) AS first_day, upper(span) - 1 AS last_day
            FROM ONLY marketpulse_demo.macro_catalog AS c
            CROSS JOIN LATERAL unnest(c.deposit_coverage) AS coverage(span)
            WHERE c.indicator_id = %s AND lower(span) <= %s AND upper(span) > %s
            ORDER BY lower(span) LIMIT 1
        """, (item.id, end, window.start)).fetchone()
        if span:
            coverage_start = max(window.start, span["first_day"])
            coverage_end = min(end, span["last_day"])
            if span["first_day"] < window.start:
                row = connection.execute("""
                    SELECT period_raw, period_start, period_end, effective_date,
                           value_decimal AS value, status_kind, status_raw
                    FROM ONLY marketpulse_demo.macro_observations
                    WHERE indicator_id = %s AND period_start BETWEEN %s AND %s
                    ORDER BY period_start DESC LIMIT 1
                """, (item.id, span["first_day"], window.start - timedelta(days=1))).fetchone()
                if row is not None:
                    anchor = MacroObservation.model_validate(row)
    observations = connection.execute("""
        SELECT period_raw, period_start, period_end, effective_date,
               value_decimal AS value, status_kind, status_raw
        FROM ONLY marketpulse_demo.macro_observations
        WHERE indicator_id = %s AND period_end >= %s AND period_start <= %s
        ORDER BY period_start LIMIT %s
    """, (item.id, window.start, end, max_points + 1)).fetchall()
    if len(observations) > max_points:
        raise HTTPException(status_code=422, detail="Macro history range exceeds the response limit")
    return MacroHistory(
        indicator=indicator_metadata(item), start=window.start, end=window.end,
        updated_at=meta["captured_at"].astimezone(timezone.utc),
        observations=[MacroObservation.model_validate(row) for row in observations],
        anchor=anchor, coverage_start=coverage_start, coverage_end=coverage_end,
    )
