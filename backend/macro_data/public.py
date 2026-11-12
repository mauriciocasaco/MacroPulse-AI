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


# Keep the shared runtime ACL policy aligned with the owner-only verifier
# in supabase/demo/macro_projection.sql. Inspect metadata as the reader.
_SHARED_SCOPE_CHECK = """
    WITH reader AS (
        SELECT r.oid, d.oid AS database_oid, r.rolinherit
        FROM pg_roles r CROSS JOIN pg_database d
        WHERE r.rolname = current_user AND d.datname = current_database()
    ), projection AS (
        SELECT c.oid FROM pg_class c
        WHERE c.relnamespace = 'marketpulse_demo'::regnamespace
            AND c.relname IN ('macro_catalog', 'macro_observations')
    ), system_acls AS (
        SELECT 'pg_proc'::regclass AS class_id, p.oid AS object_id, 0 AS column_id,
            p.proacl AS acl, acldefault('f', p.proowner) AS default_acl, false AS metadata_select
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.oid < 16384 AND p.proacl IS NOT NULL AND n.nspname IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'pg_class'::regclass, c.oid, 0, c.relacl, NULL::aclitem[], n.nspname = 'information_schema'
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.oid < 16384 AND c.relacl IS NOT NULL AND n.nspname IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'pg_class'::regclass, c.oid, a.attnum, a.attacl, NULL::aclitem[], false
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.oid < 16384 AND a.attacl IS NOT NULL AND n.nspname IN ('pg_catalog', 'information_schema')
    )
    SELECT NOT r.rolinherit
        AND NOT has_database_privilege(current_database(), 'CONNECT WITH GRANT OPTION, TEMPORARY WITH GRANT OPTION')
        AND NOT EXISTS (
            SELECT FROM pg_auth_members m
            WHERE m.member = r.oid OR m.grantor = r.oid OR (m.roleid = r.oid AND NOT (
                m.member = (SELECT nspowner FROM pg_namespace WHERE oid = 'marketpulse_demo'::regnamespace)
                AND m.grantor = 10 AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option
            ))
        )
        AND NOT EXISTS (
            SELECT FROM pg_namespace n WHERE n.nspname !~ '^pg_(toast_)?temp_[0-9]+$'
                AND has_schema_privilege(n.oid, 'CREATE, USAGE WITH GRANT OPTION')
        )
        AND NOT EXISTS (
            SELECT FROM pg_shdepend d
            WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = r.oid AND d.deptype = 'a'
                AND NOT (
                    (d.dbid = 0 AND d.classid = 'pg_database'::regclass AND d.objid = r.database_oid)
                    OR (d.dbid = r.database_oid AND d.objsubid = 0 AND (
                        (d.classid = 'pg_namespace'::regclass AND d.objid IN (
                            'marketpulse_demo'::regnamespace, COALESCE(to_regnamespace('public')::oid, 0)
                        ))
                        OR (d.classid = 'pg_class'::regclass AND d.objid IN (SELECT oid FROM projection))
                    ))
                )
        )
        AND NOT EXISTS (
            SELECT FROM pg_shdepend d
            WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = r.oid AND d.deptype = 'o'
                AND NOT (d.dbid = r.database_oid AND (
                    (d.classid = 'pg_class'::regclass AND EXISTS (
                        SELECT FROM pg_class c WHERE c.oid = d.objid AND c.relpersistence = 't'
                    ))
                    OR (d.classid = 'pg_type'::regclass AND EXISTS (
                        SELECT FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                        WHERE t.oid = d.objid AND n.nspname ~ '^pg_temp_[0-9]+$'
                    ))
                    OR d.classid = 'pg_largeobject'::regclass
                ))
        )
        AND NOT EXISTS (
            SELECT FROM pg_class c
            WHERE c.oid IN (SELECT oid FROM projection) AND (
                has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, SELECT WITH GRANT OPTION')
                OR has_any_column_privilege(c.oid, 'INSERT, UPDATE, REFERENCES, SELECT WITH GRANT OPTION')
            )
        )
        AND NOT EXISTS (
            -- Bootstrap catalog ACLs are safe to retain. Reject widened PUBLIC grants.
            SELECT FROM system_acls s
            LEFT JOIN pg_init_privs i ON i.classoid = s.class_id AND i.objoid = s.object_id
                AND i.objsubid = s.column_id AND i.privtype = 'i'
            CROSS JOIN LATERAL aclexplode(s.acl) a
            WHERE a.grantee = 0 AND NOT (s.metadata_select AND a.privilege_type = 'SELECT')
              AND NOT EXISTS (
                  SELECT FROM aclexplode(COALESCE(i.initprivs, s.default_acl)) original
                  WHERE original.grantee = 0 AND original.privilege_type = a.privilege_type
              )
        )
        -- Object ACLs can be latent even without USAGE on a schema.
        AND NOT (
            EXISTS (
                SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.oid NOT IN (SELECT oid FROM projection)
                    AND c.relpersistence <> 't'
                    AND (n.nspname NOT IN ('pg_catalog', 'information_schema') OR c.oid >= 16384)
                    AND CASE
                        WHEN c.relkind = 'S' THEN has_sequence_privilege(c.oid, 'USAGE, SELECT, UPDATE')
                        WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f', 't') THEN
                            has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN')
                            OR has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE, REFERENCES')
                        ELSE false
                    END
                    -- pg_stat_statements hides other roles' query text and IDs from
                    -- ordinary roles. Only allow its extension-owned read-only views.
                    AND NOT (
                        c.relkind = 'v' AND c.relname IN ('pg_stat_statements', 'pg_stat_statements_info')
                        AND EXISTS (
                            SELECT FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
                            WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                                AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                                AND e.extname = 'pg_stat_statements'
                        )
                        AND NOT has_table_privilege(c.oid,
                            'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, SELECT WITH GRANT OPTION')
                        AND NOT has_any_column_privilege(c.oid, 'INSERT, UPDATE, REFERENCES, SELECT WITH GRANT OPTION')
                    )
            )
        )
        -- Raw catalog statistics can contain DEV values.
        AND NOT (
            has_any_column_privilege('pg_catalog.pg_statistic', 'SELECT')
                OR has_any_column_privilege('pg_catalog.pg_statistic_ext_data', 'SELECT')
        )
        -- Keep PostgreSQL credentials and server files outside the reader scope.
        AND NOT (
            has_any_column_privilege('pg_catalog.pg_authid', 'SELECT')
                OR has_any_column_privilege('pg_catalog.pg_shadow', 'SELECT')
                OR has_any_column_privilege('pg_catalog.pg_user_mapping', 'SELECT')
                OR has_any_column_privilege('pg_catalog.pg_largeobject', 'SELECT')
                OR has_column_privilege('pg_catalog.pg_subscription', 'subconninfo', 'SELECT')
                OR EXISTS (
                    SELECT FROM pg_proc p
                    WHERE p.pronamespace = 'pg_catalog'::regnamespace
                        AND p.proname IN ('pg_read_file', 'pg_read_binary_file', 'lo_import', 'lo_export')
                        AND has_function_privilege(p.oid, 'EXECUTE')
                )
        )
        -- Native and extension-owned invoker routines can coexist with the reader.
        AND NOT (
            EXISTS (
                SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE has_schema_privilege(n.oid, 'USAGE')
                    AND has_function_privilege(p.oid, 'EXECUTE')
                    AND (
                    p.prosecdef
                    -- Block pg_net operations even if extension-owned.
                    OR (n.nspname = 'net' AND (starts_with(p.proname, 'http_')
                        OR starts_with(p.proname, '_http_') OR p.proname = 'worker_restart'))
                        OR ((n.nspname NOT IN ('pg_catalog', 'information_schema') OR p.oid >= 16384)
                            AND NOT EXISTS (
                                SELECT FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                                    AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                            ))
                    )
            )
        )
        -- Casts, domain checks and text search can dispatch callbacks by OID.
        AND NOT (
            EXISTS (
                WITH domain_refs AS (
                    SELECT d.refclassid, d.refobjid
                    FROM pg_constraint c JOIN pg_depend d ON d.objid = c.oid
                    WHERE c.contypid <> 0 AND c.contype = 'c' AND d.classid = 'pg_constraint'::regclass
                        AND d.refclassid IN ('pg_proc'::regclass, 'pg_operator'::regclass)
                ), callbacks (routine) AS (
                    SELECT castfunc FROM pg_cast WHERE castfunc <> 0
                    UNION ALL
                    SELECT refobjid FROM domain_refs WHERE refclassid = 'pg_proc'::regclass
                    UNION ALL
                    SELECT o.oprcode FROM domain_refs d JOIN pg_operator o ON o.oid = d.refobjid
                    WHERE d.refclassid = 'pg_operator'::regclass
                    UNION ALL
                    SELECT unnest(ARRAY[tmplinit, tmpllexize]) FROM pg_ts_template
                    UNION ALL
                    SELECT unnest(ARRAY[prsstart, prstoken, prsend, prsheadline, prslextype]) FROM pg_ts_parser
                )
                SELECT FROM callbacks c JOIN pg_proc p ON p.oid = c.routine
                WHERE p.prosecdef OR (p.oid >= 16384 AND NOT EXISTS (
                    SELECT FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                        AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                ))
            )
        )
        AND current_setting('lo_compat_privileges') = 'off'
        AND NOT EXISTS (
            SELECT FROM pg_largeobject_metadata lo CROSS JOIN LATERAL aclexplode(lo.lomacl) a
            WHERE lo.lomowner <> r.oid AND a.grantee IN (0, r.oid) AND a.privilege_type IN ('SELECT', 'UPDATE')
        )
        AND NOT EXISTS (SELECT FROM pg_foreign_server WHERE has_server_privilege(oid, 'USAGE'))
        AND NOT EXISTS (SELECT FROM pg_foreign_data_wrapper WHERE has_foreign_data_wrapper_privilege(oid, 'USAGE'))
        AND NOT EXISTS (SELECT FROM pg_tablespace WHERE has_tablespace_privilege(oid, 'CREATE'))
        AND NOT EXISTS (SELECT FROM pg_parameter_acl WHERE has_parameter_privilege(parname, 'SET, ALTER SYSTEM'))
    FROM reader r
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
              AND (NOT %s OR n.nspname = 'marketpulse_demo')
        """, (shared_project,)).fetchall()
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
        if cursor.execute("""
            SELECT EXISTS (
                SELECT FROM ONLY marketpulse_demo.macro_observations o
                WHERE NOT EXISTS (
                    SELECT FROM ONLY marketpulse_demo.macro_catalog c WHERE c.indicator_id = o.indicator_id
                )
            )
        """).fetchone() != (False,):
            raise ProjectionError("Public demo macro projection integrity check failed")
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
