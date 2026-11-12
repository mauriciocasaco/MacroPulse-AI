"""Local, bounded reads of the approved macroeconomic catalog."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
import os
import re
import ssl
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from ..market_data.api_config import MarketApiConfigError, _local_uvicorn, _read_dotenv, _split_dev_config
from ..market_data.dev_import import DevImportError, connect_dev
from .contracts import CATALOG, Indicator
from .repository import read_observations


MAX_DAYS = 3660
MAX_POINTS = MAX_DAYS + 1
_DSN_OPTIONS = frozenset({"host", "hostaddr", "port", "dbname", "user", "password", "sslmode", "sslrootcert"})
_ROUTE_OVERRIDES = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")
_logger = logging.getLogger(__name__)


class MacroIndicator(BaseModel):
    id: str
    title: str
    frequency: str
    unit: str
    multiplier: int
    provider: str
    series_key: str
    geography: str
    adjustment: str
    source_url: str
    reuse_url: str
    attribution: str


class MacroObservation(BaseModel):
    period_raw: str
    period_start: date
    period_end: date
    effective_date: date | None
    value: Decimal | None
    status_kind: str
    status_raw: str | None


class MacroHistory(BaseModel):
    indicator: MacroIndicator
    start: date
    end: date
    updated_at: datetime | None
    observations: list[MacroObservation]
    anchor: MacroObservation | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None


def _calendar_date(raw):
    if isinstance(raw, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        raise ValueError("expected a YYYY-MM-DD calendar date")
    return raw


class MacroWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Annotated[date, BeforeValidator(_calendar_date)]
    end: Annotated[date, BeforeValidator(_calendar_date)]

    @model_validator(mode="after")
    def check_range(self) -> MacroWindow:
        if not 0 <= (self.end - self.start).days <= MAX_DAYS:
            raise ValueError("macro history date range is invalid")
        return self


class MacroConfigError(RuntimeError):
    pass


def _loopback_dsn(value: str) -> str:
    if any(os.environ.get(key) for key in _ROUTE_OVERRIDES):
        raise MacroConfigError("unexpected PostgreSQL routing override")
    try:
        options = conninfo_to_dict(value)
        host, port = options.get("host"), options.get("port", "")
        if (set(options) - _DSN_OPTIONS or host not in {"127.0.0.1", "::1"}
                or options.get("hostaddr", host) != host
                or not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535
                or not options.get("dbname") or not options.get("user")
                or options.get("sslmode", "disable") not in {"disable", "verify-full"}):
            raise MacroConfigError("expected an explicit loopback PostgreSQL DSN")
        return make_conninfo(
            value, hostaddr=host, sslmode=options.get("sslmode", "disable"), connect_timeout=5, gssencmode="disable",
        )
    except (ValueError, psycopg.Error):
        raise MacroConfigError("invalid PostgreSQL configuration") from None


def _open_supabase_reader() -> psycopg.Connection:
    # Do not inherit libpq settings for remote connections.
    if any(key.upper().startswith("PG") for key in os.environ):
        raise MacroConfigError("unexpected PostgreSQL environment settings")
    try:
        shared = {} if os.environ.get("MARKETPULSE_DISABLE_LOCAL_DOTENV") else _read_dotenv(".env")
        config = _split_dev_config(shared)
    except MarketApiConfigError as error:
        raise MacroConfigError(str(error)) from None
    options = conninfo_to_dict(config.dsn)
    if options["port"] != "5432":
        raise MacroConfigError("Supabase DEV requires port 5432")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=options["sslrootcert"])
    except (OSError, ValueError):
        raise MacroConfigError("Supabase DEV root certificate invalid") from None
    try:
        return connect_dev(config.dsn)
    except (DevImportError, psycopg.Error, ValueError):
        raise MacroConfigError("Supabase DEV verified connection failed") from None


def open_macro_reader() -> psycopg.Connection:
    mode = os.environ.get("MACRO_API_MODE", "loopback")
    if mode == "loopback":
        connection = psycopg.connect(_loopback_dsn(os.environ.get("MACRO_API_DSN", "")))
    elif mode == "supabase-dev":
        connection = _open_supabase_reader()
    else:
        raise MacroConfigError("invalid macro connection mode")
    connection.read_only = True
    connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return connection


def indicator_metadata(item: Indicator) -> MacroIndicator:
    return MacroIndicator.model_validate(item, from_attributes=True)


def _observation(item: Indicator, row: dict) -> MacroObservation:
    confidentiality = row["confidentiality_raw"]
    permitted = confidentiality == "F" if item.provider == "ecb" else confidentiality is None
    flag = row["status_raw"]
    if flag and not re.fullmatch(r"[a-zA-Z ]{1,32}", flag):
        flag = None
    return MacroObservation(
        period_raw=row["period_raw"], period_start=row["period_start"], period_end=row["period_end"],
        effective_date=row["effective_date"], value=row["value_decimal"] if permitted else None,
        status_kind=row["status_kind"] if permitted else "withheld", status_raw=flag if permitted else None,
    )


def _deposit_coverage(connection: psycopg.Connection, indicator_id: str, window: MacroWindow, schema: str):
    # Successful snapshots establish which dates were queried. Clip at the
    # provider-local date of receipt so future periods never become coverage.
    return connection.execute(sql.SQL("""
        WITH intervals AS (
            SELECT requested_start AS first_day,
                   least(requested_end, (captured_at AT TIME ZONE 'Europe/Berlin')::date) AS last_day
            FROM {}.capture
            WHERE indicator_id = %s AND outcome = 'success' AND stale_observations = 0
              AND source_attributes->>'version_scope' IS DISTINCT FROM 'provider_repository'
              AND requested_start <= %s
        ), merged AS (
            SELECT range_agg(daterange(first_day, last_day, '[]')) AS dates
            FROM intervals WHERE first_day <= last_day
        )
        SELECT lower(span), upper(span) - 1
        FROM merged CROSS JOIN LATERAL unnest(dates) AS coverage(span)
        WHERE upper(span) > %s
        ORDER BY lower(span)
        LIMIT 1
    """).format(sql.Identifier(schema)), (indicator_id, window.end, window.start)).fetchone()


def load_history(
    connection: psycopg.Connection, item: Indicator, window: MacroWindow, *, schema: str = "macro_data",
) -> MacroHistory:
    """Load source observations and locally verified deposit-rate coverage."""
    connection.execute("SET LOCAL statement_timeout = '5s'")
    connection.execute("SET LOCAL lock_timeout = '2s'")
    updated_at, today = connection.execute(sql.SQL("""
        SELECT max(captured_at), (statement_timestamp() AT TIME ZONE 'Europe/Berlin')::date
        FROM {}.capture
        WHERE indicator_id = %s AND outcome = 'success'
          AND source_attributes->>'version_scope' IS DISTINCT FROM 'provider_repository'
    """).format(sql.Identifier(schema)), (item.id,)).fetchone()
    coverage_start = coverage_end = anchor = None
    end = window.end
    if item.frequency == "effective_date":
        end = min(end, today)
        span = _deposit_coverage(connection, item.id, window, schema)
        if span is not None:
            first, last = span
            coverage_start, coverage_end = max(first, window.start), min(last, end)
            if coverage_start > coverage_end:
                coverage_start = coverage_end = None
            if coverage_start is not None and first < window.start:
                previous = read_observations(
                    connection, item.id, start=first, end=window.start - timedelta(days=1), limit=1, newest_first=True, schema=schema,
                )
                if previous:
                    anchor = _observation(item, previous[0])
    rows = read_observations(connection, item.id, start=window.start, end=end, limit=MAX_POINTS + 1, schema=schema)
    if len(rows) > MAX_POINTS:
        raise HTTPException(status_code=422, detail="Macro history range exceeds the response limit")
    return MacroHistory(
        indicator=indicator_metadata(item), start=window.start, end=window.end,
        updated_at=updated_at.astimezone(timezone.utc) if updated_at is not None else None,
        observations=[_observation(item, row) for row in rows], anchor=anchor,
        coverage_start=coverage_start, coverage_end=coverage_end,
    )


class _MacroRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(status_code=422, detail="Invalid macro request") from None

        return sanitized


def _require_local(request: Request) -> None:
    if not _local_uvicorn(request.scope):
        raise HTTPException(status_code=404, detail="Macro data route unavailable")


router = APIRouter(prefix="/macro", dependencies=[Depends(_require_local)], route_class=_MacroRoute)


@router.get("/indicators")
def indicators(request: Request, response: Response) -> list[MacroIndicator]:
    if request.query_params:
        raise HTTPException(status_code=422, detail="Invalid macro request")
    from ..macro_ai.config import research_v2_enabled

    if research_v2_enabled():
        response.headers["X-MarketPulse-Research-V2"] = "true"
        response.headers["Cache-Control"] = "no-store"
    return [indicator_metadata(item) for item in CATALOG.values()]


@router.get("/indicators/{indicator_id}/history")
def history(indicator_id: str, query: Annotated[MacroWindow, Query()], request: Request) -> MacroHistory:
    item = CATALOG.get(indicator_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Macro indicator not found")
    if len(request.query_params.multi_items()) != 2:
        raise HTTPException(status_code=422, detail="Invalid macro request")
    try:
        with open_macro_reader() as connection:
            return load_history(connection, item, query)
    except MacroConfigError as error:
        _logger.warning("Macro reader configuration or connection failure (%s)", error)
        raise HTTPException(status_code=503, detail="Macro data unavailable") from None
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Macro data unavailable") from None
