"""Read and verify the separate, sanitized DEMO macro projection."""

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


def verify_projection(connection: psycopg.Connection) -> None:
    with connection.cursor(row_factory=tuple_row) as cursor:
        permitted = cursor.execute("""
            SELECT to_regnamespace('macro_data') IS NULL
                AND to_regnamespace('market_data') IS NULL
                AND to_regnamespace('news') IS NULL
                AND has_table_privilege(to_regclass('marketpulse_demo.macro_catalog'), 'SELECT')
                AND has_table_privilege(to_regclass('marketpulse_demo.macro_observations'), 'SELECT')
        """).fetchone()
        if permitted != (True,):
            raise ProjectionError("Public demo macro projection is unavailable")
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
