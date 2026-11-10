"""Read and verify the separate, sanitized DEMO macro projection."""

from __future__ import annotations

from datetime import date, timedelta, timezone

from fastapi import HTTPException
import psycopg
from psycopg.rows import tuple_row

from .api import MacroHistory, MacroObservation, MacroWindow, indicator_metadata
from .contracts import CATALOG, Indicator


_ALLOWED_RELATIONS = {
    ("marketpulse_demo", "synthetic_bars", "r"),
    ("marketpulse_demo", "macro_catalog", "r"),
    ("marketpulse_demo", "macro_observations", "r"),
}
_PROJECTION_COLUMNS = {
    "macro_catalog": {"indicator_id", "series_key", "captured_at", "deposit_coverage"},
    "macro_observations": {
        "indicator_id", "period_raw", "period_start", "period_end", "effective_date",
        "value_decimal", "status_kind", "status_raw",
    },
}


class ProjectionError(RuntimeError):
    """A fixed, non-sensitive DEMO projection validation failure."""


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
        if set(catalog) != expected:
            raise ProjectionError("Public demo macro projection is incomplete")


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
