"""Restricted API for the isolated public demonstration."""

from asyncio import timeout
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
import hmac
from ipaddress import ip_address
import os
from pathlib import Path as FilePath
import re
from time import monotonic
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row, tuple_row

from .api import AssetResponse, DailyBar, HistoryResponse, QueryDate
from .macro_data.api import MacroHistory, MacroIndicator, MacroWindow, indicator_metadata
from .macro_data.contracts import CATALOG
from .macro_data.public import read_history as read_macro_history, verify_projection
from .market_data.api_config import _ROLE_CHECK, _WRITE_PRIVILEGES, is_dev_target


ORIGIN_HEADER = "x-marketpulse-origin"
CLIENT_IP_HEADER = "x-marketpulse-client-ip"
MAX_WINDOW_DAYS = 2600
MAX_BODY = 8192
SOURCE = "marketpulse_synthetic"
CONVENTION = "synthetic:marketpulse-demo-v1"
SERIES = ("Synthetic equity index", "Synthetic energy index", "Synthetic metal index")
_HISTORY = re.compile(r"/assets/[1-3]/history")
_RO_ROLE = "marketpulse_demo_ro"
_MACRO_CATALOG_PATH = "/macro/indicators"
_MACRO_HISTORY_PATHS = frozenset(f"{_MACRO_CATALOG_PATH}/{key}/history" for key in CATALOG)
_NON_DEMO_SETTINGS = frozenset({
    "MARKET_API_DSN", "MACRO_API_DSN", "MACRO_API_MODE", "DATABASE_URL", "MARKETPULSE_DEV_PROJECT_REF",
    "USER_RO", "PASS_USER_RO", "HOST_RO", "PORT_RO", "DB_RO", "SSL_MODE_RO", "SSL_ROOT_CERT_RO",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "MARKET_ANALYSIS_EMBEDDING_CACHE",
    "MARKETPULSE_MACRO_AI_ENABLED", "MARKETPULSE_GEMINI_MODEL",
})


class DemoConfigurationError(RuntimeError):
    """A fixed, non-sensitive startup failure reason."""


@dataclass(frozen=True)
class DemoSettings:
    dsn: str = field(repr=False)
    origin_secret: str = field(repr=False)
    catalog_per_minute: int = 30
    history_per_minute: int = 20
    concurrency: int = 4
    macro_enabled: bool = False


def _limit(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, str(default))
    if not re.fullmatch(r"[1-9][0-9]?", raw) or int(raw) > default:
        raise DemoConfigurationError("Public demo limit is invalid")
    return int(raw)


def resolve_demo_settings(env: Mapping[str, str]) -> DemoSettings:
    for key in ("DEBUG", "MARKETPULSE_DEBUG", "MARKETPULSE_DEMO_ANALYSIS_ENABLED"):
        if env.get(key, "false") != "false":
            raise DemoConfigurationError("Public demo debug and analysis must be disabled")
    if _NON_DEMO_SETTINGS.intersection(env) or any(
        key.upper().startswith("PG") for key in env
    ):
        raise DemoConfigurationError("Public demo must use only dedicated settings")
    macro_enabled = env.get("MARKETPULSE_DEMO_MACRO_ENABLED", "false")
    if macro_enabled not in {"true", "false"}:
        raise DemoConfigurationError("Public demo macro feature flag is invalid")

    secret = env.get("MARKETPULSE_DEMO_ORIGIN_SECRET", "")
    if (not re.fullmatch(r"[a-f0-9]{64}", secret) or len(set(secret)) < 12
            or re.fullmatch(r"(.{1,32})\1+", secret)):
        raise DemoConfigurationError("Public demo origin secret is missing or invalid")
    ref = env.get("MARKETPULSE_DEMO_PROJECT_REF", "")
    if (not re.fullmatch(r"[a-z]{20}", ref) or is_dev_target(ref, "") or len(set(ref)) < 6
            or re.search(r"example|placeholder|replace|dummy|sample", ref)):
        raise DemoConfigurationError("Public demo project reference is missing or invalid")
    try:
        options = conninfo_to_dict(env.get("MARKETPULSE_DEMO_DSN", ""))
    except (ValueError, psycopg.Error):
        raise DemoConfigurationError("Public demo DSN is invalid") from None
    if options.keys() != {"host", "port", "user", "password", "dbname"}:
        raise DemoConfigurationError("Public demo DSN must specify only the required connection fields")
    host, user, password = options["host"], options["user"], options["password"]
    if (is_dev_target(host, user) or options["port"] != "5432" or options["dbname"] != "postgres"
            or len(password) < 24 or len(set(password)) < 12
            or re.search(r"example|placeholder|replace|changeme|password|dummy|sample", password, re.I)):
        raise DemoConfigurationError("Public demo DSN target or role is invalid")
    direct = host == f"db.{ref}.supabase.co" and user == _RO_ROLE
    session_pooler = (
        re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host) and user == f"{_RO_ROLE}.{ref}"
    )
    if not (direct or session_pooler):
        raise DemoConfigurationError("Public demo DSN target or role is invalid")
    ca = FilePath(env.get("MARKETPULSE_DEMO_SSL_ROOT_CERT", ""))
    if not ca.is_absolute() or not ca.is_file() or not os.access(ca, os.R_OK):
        raise DemoConfigurationError("Public demo Supabase CA file is missing or unreadable")
    dsn = make_conninfo(**options, sslmode="verify-full", sslrootcert=str(ca),
                        gssencmode="disable", connect_timeout=5)
    return DemoSettings(dsn, secret, _limit(env, "MARKETPULSE_DEMO_CATALOG_PER_MINUTE", 30),
                        _limit(env, "MARKETPULSE_DEMO_HISTORY_PER_MINUTE", 20),
                        _limit(env, "MARKETPULSE_DEMO_CONCURRENCY", 4), macro_enabled == "true")


def connect_demo(dsn: str, *, macro_enabled: bool = False) -> psycopg.Connection:
    connection = psycopg.connect(dsn, row_factory=dict_row)
    try:
        if not connection.pgconn.ssl_in_use:
            raise DemoConfigurationError("Public demo PostgreSQL requires TLS")
        connection.read_only = True
        if macro_enabled:
            connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '3s'")
        connection.execute("SET LOCAL lock_timeout = '1s'")
        connection.execute("SET LOCAL search_path = pg_catalog")
        with connection.cursor(row_factory=tuple_row) as cursor:
            verified = cursor.execute(_ROLE_CHECK, (_WRITE_PRIVILEGES,)).fetchone()
            scope = cursor.execute("""
                SELECT current_user = %s
                    AND NOT has_database_privilege(current_database(), 'TEMP')
                    AND COALESCE(has_schema_privilege(to_regnamespace('marketpulse_demo'), 'USAGE'), false)
                    AND COALESCE(has_table_privilege(to_regclass('marketpulse_demo.synthetic_bars'), 'SELECT'), false)
                """, (_RO_ROLE,)).fetchone()
        if verified != (True,) or scope != (True,):
            raise DemoConfigurationError("Public demo PostgreSQL role lacks required read-only scope")
        if macro_enabled:
            verify_projection(connection)
    except Exception:
        connection.close()
        raise
    return connection


class DemoWindow(BaseModel):
    start: QueryDate
    end_exclusive: QueryDate

    @model_validator(mode="after")
    def validate_dates(self) -> "DemoWindow":
        if not 1 <= (self.end_exclusive - self.start).days <= MAX_WINDOW_DAYS:
            raise ValueError("Invalid demo history window")
        return self

    model_config = {"extra": "forbid"}


def _asset(series: int, first: date | None, last: date | None, count: int) -> AssetResponse:
    return AssetResponse(
        asset_source_id=series, asset_id=f"synthetic_{series}", name=SERIES[series - 1],
        source_id=SOURCE, provider_name="MarketPulse AI synthetic dataset",
        symbol=f"DEMO-{series}", economic_unit="Synthetic index points",
        calendar_code="SYNTHETIC_DAILY", first_date=first, last_date=last, bar_count=count,
    )


class DemoRepository:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def catalog(self) -> list[AssetResponse]:
        with connect_demo(self._dsn) as connection:
            rows = connection.execute("""
                SELECT series_id, min(bar_date) AS first, max(bar_date) AS last, count(*) AS count
                FROM marketpulse_demo.synthetic_bars
                WHERE series_id BETWEEN 1 AND 3 GROUP BY series_id ORDER BY series_id
            """).fetchall()
        return [_asset(row["series_id"], row["first"], row["last"], row["count"]) for row in rows]

    def history(self, series: int, window: DemoWindow) -> HistoryResponse:
        with connect_demo(self._dsn) as connection:
            coverage = connection.execute("""
                SELECT min(bar_date) AS first, max(bar_date) AS last, count(*) AS count
                FROM marketpulse_demo.synthetic_bars WHERE series_id = %s
            """, (series,)).fetchone()
            if not coverage or not coverage["count"]:
                raise HTTPException(status_code=404, detail="Asset unavailable")
            rows = connection.execute("""
                SELECT bar_date, close, generated_at FROM marketpulse_demo.synthetic_bars
                WHERE series_id = %s AND bar_date >= %s AND bar_date < %s
                ORDER BY bar_date LIMIT %s
            """, (series, window.start, window.end_exclusive, MAX_WINDOW_DAYS)).fetchall()
        asset = _asset(series, coverage["first"], coverage["last"], coverage["count"])
        return HistoryResponse(asset=asset, bars=[
            DailyBar(
                asset_source_id=series, asset_id=asset.asset_id, source_id=SOURCE, symbol=asset.symbol,
                bar_date=row["bar_date"], close=row["close"], retrieved_at=row["generated_at"],
                adjusted_close=None, adjusted_close_basis=None, interval="1d",
                open=None, high=None, low=None, volume=None, ohlc_basis="provider_unadjusted",
                session_status="unknown", quality_status="partial", source_convention=CONVENTION,
                provider=SOURCE, adapter_version="synthetic-v1", provider_version=None,
                volume_unit=None, exchange_trade_date=None, settlement_time=None,
                availability_time=None, rolling_method=None,
            ) for row in rows
        ])


class _Quota:
    """One bounded table per operation, cleared at fixed minute boundaries."""

    def __init__(self, limit: int):
        self.limit = limit
        self.window = -1
        self.counts: dict[bytes, int] = {}

    def retry_after(self, key: bytes) -> int | None:
        second = int(monotonic())
        minute, offset = divmod(second, 60)
        if minute != self.window:
            self.window = minute
            self.counts.clear()
        count = self.counts.get(key, 0)
        if count >= self.limit or (not count and len(self.counts) >= 4096):
            return 60 - offset
        self.counts[key] = count + 1
        return None


def _error(status: int, detail: str, retry: int | None = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry is not None:
        headers["Retry-After"] = str(retry)
    return JSONResponse(status_code=status, content={"detail": detail}, headers=headers)


class _PublicAnalysisInput(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    asset: str = Field(min_length=1, max_length=32)
    event: str = Field(min_length=1, max_length=16)
    start: QueryDate
    end_exclusive: QueryDate
    model_config = {"extra": "forbid"}


def create_public_app() -> FastAPI:
    settings = resolve_demo_settings(os.environ)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            with (connect_demo(settings.dsn, macro_enabled=True) if settings.macro_enabled
                  else connect_demo(settings.dsn)):
                pass
        except Exception:
            raise DemoConfigurationError("Public demo database startup verification failed") from None
        yield

    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False, lifespan=lifespan)
    quotas = {"catalog": _Quota(settings.catalog_per_minute), "history": _Quota(settings.history_per_minute)}
    in_flight = 0

    def repository() -> DemoRepository:
        return DemoRepository(settings.dsn)

    @app.middleware("http")
    async def restrict(request: Request, call_next):
        nonlocal in_flight
        credentials = request.headers.getlist(ORIGIN_HEADER)
        if len(credentials) != 1 or not hmac.compare_digest(
            credentials[0].encode("latin-1"), settings.origin_secret.encode("ascii")
        ):
            return _error(403, "Forbidden")
        ips = request.headers.getlist(CLIENT_IP_HEADER)
        try:
            if len(ips) != 1 or len(ips[0]) > 45 or "%" in ips[0]:
                raise ValueError
            address = ip_address(ips[0])
            if address.version == 6 and address.ipv4_mapped:
                address = address.ipv4_mapped
        except ValueError:
            return _error(403, "Forbidden")

        path = request.scope["path"]
        raw_path = request.scope.get("raw_path", b"")
        if raw_path != path.encode("utf-8") or len(request.scope["query_string"]) > 256:
            return _error(400, "Invalid request")
        operation = "catalog" if path == "/assets" else "history" if _HISTORY.fullmatch(path) else None
        if settings.macro_enabled:
            if path == _MACRO_CATALOG_PATH:
                operation = "catalog"
            elif path in _MACRO_HISTORY_PATHS:
                operation = "history"
        analysis = path == "/analysis"
        if not (operation or analysis):
            return _error(404, "Not found")
        if request.method != ("POST" if analysis else "GET"):
            return _error(405, "Method not allowed")
        if (analysis or operation == "catalog") and request.scope["query_string"]:
            return _error(400, "Invalid request")
        if operation == "history" and len(request.query_params.multi_items()) != len(request.query_params):
            return _error(400, "Invalid request")
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0])):
            return _error(400, "Invalid request")
        if lengths and int(lengths[0]) > (MAX_BODY if analysis else 0):
            return _error(413, "Request too large")
        if analysis and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return _error(415, "Expected JSON")
        if in_flight >= settings.concurrency:
            return _error(503, "Public demo is busy", 1)
        if operation:
            client_key = hmac.digest(settings.origin_secret.encode("ascii"), address.packed, "sha256")
            retry = quotas[operation].retry_after(client_key)
            if retry is not None:
                return _error(429, "Rate limit exceeded", retry)
        in_flight += 1
        try:
            body = bytearray()
            async with timeout(3):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > (MAX_BODY if analysis else 0):
                        return _error(413, "Request too large")
                    body.extend(chunk)
            if analysis:
                try:
                    _PublicAnalysisInput.model_validate_json(body)
                except ValidationError:
                    return _error(422, "Invalid request parameters")
                return _error(503, "Public demo analysis is disabled")
            response = await call_next(request)
        except TimeoutError:
            return _error(408, "Request timed out")
        except Exception:
            return _error(503, "Public demo service unavailable")
        finally:
            in_flight -= 1
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _error_obj: RequestValidationError):
        return _error(422, "Invalid request parameters")

    @app.get("/assets")
    def catalog(repo: Annotated[DemoRepository, Depends(repository)]) -> list[AssetResponse]:
        return repo.catalog()

    @app.get("/assets/{series}/history")
    def history(series: Annotated[int, Path(ge=1, le=3)], window: Annotated[DemoWindow, Query()],
                repo: Annotated[DemoRepository, Depends(repository)]) -> HistoryResponse:
        return repo.history(series, window)

    if settings.macro_enabled:
        @app.get(_MACRO_CATALOG_PATH)
        def macro_catalog() -> list[MacroIndicator]:
            with connect_demo(settings.dsn, macro_enabled=True):
                return [indicator_metadata(item) for item in CATALOG.values()]

        @app.get(f"{_MACRO_CATALOG_PATH}/{{indicator_id}}/history")
        def macro_history(indicator_id: str, window: Annotated[MacroWindow, Query()]) -> MacroHistory:
            if (window.end - window.start).days >= MAX_WINDOW_DAYS:
                raise HTTPException(status_code=422, detail="Invalid request parameters")
            with connect_demo(settings.dsn, macro_enabled=True) as connection:
                return read_macro_history(connection, CATALOG[indicator_id], window, max_points=MAX_WINDOW_DAYS)

    return app
