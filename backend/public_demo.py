"""Restricted API for the isolated public MacroPulse demonstration."""

from __future__ import annotations

from asyncio import get_running_loop, shield, timeout
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hmac
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
from time import monotonic, time
from typing import Annotated, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row, tuple_row

from .macro_data.api import MacroHistory, MacroWindow, indicator_metadata
from .macro_data.contracts import CATALOG
from .macro_data.public import read_history, verify_projection
from .market_data.api_config import _ROLE_CHECK, _WRITE_PRIVILEGES, is_dev_target

if TYPE_CHECKING:
    from .macro_ai.models import IndicatorId, ResearchRequest

ORIGIN_HEADER = "x-marketpulse-origin"
CLIENT_IP_HEADER = "x-marketpulse-client-ip"
RESEARCH_HEADER = "X-MacroPulse-Public-Research"
MAX_WINDOW_DAYS = 2600
MAX_BODY = 4096
MAX_RESEARCH_RESPONSE = 64 * 1024
RESEARCH_SECONDS = 40
RO_ROLE = "marketpulse_demo_ro"
SHARED_PROJECT_REF = "swpithkjviavycrxpwed"
_MACRO_CATALOG_PATH = "/macro/indicators"
_MACRO_HISTORY_PATHS = frozenset(f"{_MACRO_CATALOG_PATH}/{key}/history" for key in CATALOG)
_RESEARCH_PATH = "/macro/analysis"
_NON_DEMO_SETTINGS = frozenset({
    "MARKET_API_DSN", "MACRO_API_DSN", "MACRO_API_MODE", "DATABASE_URL", "MARKETPULSE_DEV_PROJECT_REF",
    "USER_RO", "PASS_USER_RO", "HOST_RO", "PORT_RO", "DB_RO", "SSL_MODE_RO", "SSL_ROOT_CERT_RO",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "MARKET_ANALYSIS_EMBEDDING_CACHE",
    "MARKETPULSE_MACRO_AI_ENABLED", "MARKETPULSE_GEMINI_MODEL",
    "MARKETPULSE_RESEARCH_V2_ENABLED", "MARKETPULSE_RESEARCH_V2_RETRIEVER",
    "MARKETPULSE_RESEARCH_V2_E5_CACHE", "MARKETPULSE_RESEARCH_V2_E5_INDEX",
    "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY",
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
    shared_project: bool = False


def _limit(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, str(default))
    if not re.fullmatch(r"[1-9][0-9]?", raw) or int(raw) > default:
        raise DemoConfigurationError("Public demo limit is invalid")
    return int(raw)


def demo_target(
    value: str, ref: str, role: str, *, direct_only: bool = False, shared_project: bool = False,
    shared_role: str = RO_ROLE,
) -> dict[str, str]:
    """Validate explicit connection parameters before constructing a DEMO DSN."""
    if (not re.fullmatch(r"[a-z]{20}", ref) or len(set(ref)) < 6
            or re.search(r"example|placeholder|replace|dummy|sample", ref)):
        raise DemoConfigurationError("Public demo project reference is missing or invalid")
    if shared_project:
        if ref != SHARED_PROJECT_REF or role != shared_role:
            raise DemoConfigurationError("Public demo shared project reference or role is invalid")
    elif ref == SHARED_PROJECT_REF or is_dev_target(ref, ""):
        raise DemoConfigurationError("Public demo project reference is missing or invalid")
    try:
        options = conninfo_to_dict(value)
    except (ValueError, psycopg.Error):
        raise DemoConfigurationError("Public demo DSN is invalid") from None
    if options.keys() != {"host", "port", "user", "password", "dbname"}:
        raise DemoConfigurationError("Public demo DSN must specify only the required connection fields")
    host, user, password = options["host"], options["user"], options["password"]
    if ((not shared_project and is_dev_target(host, user))
            or options["port"] != "5432" or options["dbname"] != "postgres"
            or len(password) < 24 or len(set(password)) < 12
            or re.search(r"example|placeholder|replace|changeme|password|dummy|sample", password, re.I)):
        raise DemoConfigurationError("Public demo DSN target or role is invalid")
    direct = host == f"db.{ref}.supabase.co" and user == role
    session_pooler = (not direct_only and re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host)
                      and user == f"{role}.{ref}")
    if not (direct or session_pooler):
        raise DemoConfigurationError("Public demo DSN target or role is invalid")
    return options


def tls_dsn(options: dict[str, str], ca_value: str) -> str:
    ca = Path(ca_value)
    if not ca.is_absolute() or not ca.is_file() or not os.access(ca, os.R_OK):
        raise DemoConfigurationError("Public demo Supabase CA file is missing or unreadable")
    return make_conninfo(**options, sslmode="verify-full", sslrootcert=str(ca),
                         gssencmode="disable", connect_timeout=5)


def resolve_demo_settings(env: Mapping[str, str], *, verified_dsn: str | None = None) -> DemoSettings:
    for key in ("DEBUG", "MARKETPULSE_DEBUG", "MARKETPULSE_DEMO_ANALYSIS_ENABLED",
                "LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2"):
        if env.get(key, "false") != "false":
            raise DemoConfigurationError("Public demo debug, tracing and legacy analysis must be disabled")
    if _NON_DEMO_SETTINGS.intersection(env) or any(key.upper().startswith("PG") for key in env):
        raise DemoConfigurationError("Public demo must use only dedicated settings")
    if env.get("MARKETPULSE_DEMO_MACRO_ENABLED", "true") != "true":
        raise DemoConfigurationError("Public demo requires the macro projection")
    secret = env.get("MARKETPULSE_DEMO_ORIGIN_SECRET", "")
    if (not re.fullmatch(r"[a-f0-9]{64}", secret) or len(set(secret)) < 12
            or re.fullmatch(r"(.{1,32})\1+", secret)):
        raise DemoConfigurationError("Public demo origin secret is missing or invalid")
    shared = env.get("MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT", "false")
    if shared not in ("false", "true"):
        raise DemoConfigurationError("Public demo shared project flag is invalid")
    dsn = verified_dsn
    if dsn is None:
        options = demo_target(
            env.get("MARKETPULSE_DEMO_DSN", ""), env.get("MARKETPULSE_DEMO_PROJECT_REF", ""), RO_ROLE,
            shared_project=shared == "true",
        )
        dsn = tls_dsn(options, env.get("MARKETPULSE_DEMO_SSL_ROOT_CERT", ""))
    return DemoSettings(dsn, secret, _limit(env, "MARKETPULSE_DEMO_CATALOG_PER_MINUTE", 30),
                        _limit(env, "MARKETPULSE_DEMO_HISTORY_PER_MINUTE", 20),
                        _limit(env, "MARKETPULSE_DEMO_CONCURRENCY", 4), shared_project=shared == "true")


def connect_demo(settings: DemoSettings) -> psycopg.Connection:
    connection = psycopg.connect(settings.dsn, row_factory=dict_row)
    try:
        sslmode = next((option.val for option in connection.pgconn.info if option.keyword == b"sslmode"), None)
        if not connection.pgconn.ssl_in_use or sslmode != b"verify-full":
            raise DemoConfigurationError("Public demo PostgreSQL requires TLS verify-full")
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '2s'")
        connection.execute("SET LOCAL lock_timeout = '1s'")
        connection.execute("SET LOCAL transaction_timeout = '20s'")
        connection.execute("SET LOCAL search_path = pg_catalog")
        with connection.cursor(row_factory=tuple_row) as cursor:
            verified = cursor.execute(_ROLE_CHECK, (_WRITE_PRIVILEGES,)).fetchone()
            scope = cursor.execute("""
                SELECT current_user = %s
                    AND COALESCE(has_schema_privilege(to_regnamespace('marketpulse_demo'), 'USAGE'), false)
                    AND (%s OR (
                        NOT has_database_privilege(current_database(), 'TEMP')
                        AND NOT EXISTS (
                            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'
                                AND has_schema_privilege(n.oid, 'USAGE') AND has_function_privilege(p.oid, 'EXECUTE')
                        )
                    ))
                """, (RO_ROLE, settings.shared_project)).fetchone()
        if verified != (True,) or scope != (True,):
            raise DemoConfigurationError("Public demo PostgreSQL role lacks required read-only scope")
        verify_projection(connection, shared_project=settings.shared_project)
    except Exception:
        connection.close()
        raise
    return connection


class _Quota:
    """One bounded table per read operation, cleared at fixed minute boundaries."""

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


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON key")
    return result


def parse_research(body: bytes, *, online: bool = False) -> ResearchRequest:
    from .macro_ai.models import OnlineResearchRequest, PublicResearchRequest, ResearchRequest

    payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(payload, dict) or any(type(value) is not str for value in payload.values()):
        raise ValueError("Invalid Research request")
    contract = OnlineResearchRequest if online else PublicResearchRequest
    model = contract.model_validate(payload)
    return ResearchRequest.model_validate(model.model_dump(exclude={"research_version"}))


def _research_config(env: Mapping[str, str]) -> tuple[str, str] | None:
    if env.get("MARKETPULSE_DEMO_RESEARCH_ENABLED") != "true":
        return None
    try:
        from .macro_ai.public_gemini import MODELS
    except ImportError:
        return None

    key = env.get("MARKETPULSE_DEMO_GEMINI_API_KEY", "")
    model = env.get("MARKETPULSE_DEMO_GEMINI_MODEL", "gemini-3.6-flash")
    # AI Studio authorization keys use AQ.; legacy standard keys use AIza.
    valid_key = re.fullmatch(r"(?:AIza[A-Za-z0-9_-]{35,80}|AQ\.[A-Za-z0-9_-]{32,160})", key)
    if not valid_key or model not in MODELS:
        return None
    return key, model


def run_public_research(settings: DemoSettings, request: ResearchRequest, key: str, model: str, deadline: float):
    from .macro_ai.public_gemini import PublicGemini
    from .macro_ai.v2_service import ResearchRuntime, run

    provider = PublicGemini(api_key=key, model=model, deadline=deadline)
    # A single verified read-only transaction supplies all deterministic
    # operands for this investigation. No local reader is registered.
    with connect_demo(settings) as connection:
        def read(indicator: IndicatorId, window: MacroWindow) -> MacroHistory:
            if monotonic() >= deadline:
                raise TimeoutError
            return read_history(connection, CATALOG[indicator], window, max_points=MAX_WINDOW_DAYS)

        result = run(request, runtime=ResearchRuntime(read, provider.interpret, provider.compose, model))
    if monotonic() >= deadline:
        raise TimeoutError
    return result


def create_public_app(
    *, settings: DemoSettings | None = None,
    connect: Callable | None = None, history_reader: Callable | None = None,
    catalog_reader: Callable | None = None, research_runner: Callable | None = None,
    research_parser: Callable | None = None, max_window_days: int = MAX_WINDOW_DAYS - 1,
) -> FastAPI:
    settings = settings or resolve_demo_settings(os.environ)

    def open_reader():
        return (connect or connect_demo)(settings)

    history_reader = history_reader or (
        lambda connection, item, window: read_history(connection, item, window, max_points=MAX_WINDOW_DAYS)
    )
    research_parser = research_parser or parse_research
    research = _research_config(os.environ)
    if research:
        try:
            from .macro_ai.v2_retrieval import lexical
            from .macro_ai.v2_service import workflow

            lexical()
            workflow()
        except Exception:
            research = None
    db_password = conninfo_to_dict(settings.dsn)["password"] if research else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            with open_reader() as connection:
                if catalog_reader:
                    catalog_reader(connection)
        except Exception:
            raise DemoConfigurationError("MacroPulse database startup verification failed") from None
        yield

    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False, lifespan=lifespan)
    quotas = {"catalog": _Quota(settings.catalog_per_minute), "history": _Quota(settings.history_per_minute)}
    read_in_flight = 0
    research_tasks = set()

    @app.post(_RESEARCH_PATH)
    async def respond_to_research(request: Request) -> Response:
        if research is None:
            return _error(503, "Public Research is unavailable")
        expires = request.headers.getlist("x-macropulse-research-expires")
        if len(expires) != 1 or not re.fullmatch(r"[0-9]{13}", expires[0]):
            return _error(403, "Forbidden")
        remaining = int(expires[0]) / 1000 - time()
        if not RESEARCH_SECONDS + 2 < remaining <= 65:
            return _error(403, "Forbidden")
        try:
            params = research_parser(request.state.research_body)
            if settings.origin_secret in params.question or (db_password and db_password in params.question):
                raise ValueError("Sensitive input")
        except ValueError:
            response = _error(422, "Invalid request parameters")
            response.headers["X-MacroPulse-Research-Complete"] = "true"
            return response
        if len(research_tasks) >= 2:
            return _error(503, "Public Research is busy", 1)
        deadline = monotonic() + RESEARCH_SECONDS
        task = get_running_loop().run_in_executor(
            None, research_runner or run_public_research, settings, params, *research, deadline,
        )
        research_tasks.add(task)

        def settled(finished):
            research_tasks.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(settled)
        try:
            async with timeout(RESEARCH_SECONDS):
                result = await shield(task)
            payload = result.model_dump_json().encode("utf-8")
            if len(payload) > MAX_RESEARCH_RESPONSE:
                return _error(503, "Public Research is unavailable")
            return Response(payload, media_type="application/json", headers={
                "Cache-Control": "no-store", "X-MacroPulse-Research-Complete": "true",
            })
        except TimeoutError:
            return _error(504, "Public Research timed out")
        except Exception:
            return _error(503, "Public Research is unavailable")

    @app.middleware("http")
    async def restrict(request: Request, call_next):
        nonlocal read_in_flight
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
        operation = "catalog" if path == _MACRO_CATALOG_PATH else "history" if path in _MACRO_HISTORY_PATHS else None
        analysis = path == _RESEARCH_PATH
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
        max_body = MAX_BODY if analysis else 0
        if lengths and int(lengths[0]) > max_body:
            return _error(413, "Request too large")
        types = request.headers.getlist("content-type")
        if analysis and (len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json"
                         or request.headers.get("content-encoding")):
            return _error(415, "Expected JSON")
        try:
            body = bytearray()
            async with timeout(3):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > max_body:
                        return _error(413, "Request too large")
                    body.extend(chunk)
        except TimeoutError:
            return _error(408, "Request timed out")
        except Exception:
            return _error(400, "Invalid request")
        if analysis:
            request.state.research_body = body
            return await call_next(request)
        if read_in_flight >= settings.concurrency:
            return _error(503, "MacroPulse is busy", 1)
        client_key = hmac.digest(settings.origin_secret.encode("ascii"), address.packed, "sha256")
        retry = quotas[operation].retry_after(client_key)
        if retry is not None:
            return _error(429, "Rate limit exceeded", retry)
        read_in_flight += 1
        try:
            response = await call_next(request)
        except Exception:
            return _error(503, "MacroPulse service unavailable" if connect else "Public demo service unavailable")
        finally:
            read_in_flight -= 1
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _error_obj: RequestValidationError):
        return _error(422, "Invalid request parameters")

    @app.get(_MACRO_CATALOG_PATH)
    def macro_catalog(response: Response):
        with open_reader() as connection:
            if research is not None:
                response.headers[RESEARCH_HEADER] = "true"
            return (catalog_reader(connection) if catalog_reader else
                    [indicator_metadata(item) for item in CATALOG.values()])

    @app.get(f"{_MACRO_CATALOG_PATH}/{{indicator_id}}/history")
    def macro_history(indicator_id: str, window: Annotated[MacroWindow, Query()]) -> MacroHistory:
        if (window.end - window.start).days > max_window_days:
            raise HTTPException(status_code=422, detail="Invalid request parameters")
        with open_reader() as connection:
            return history_reader(connection, CATALOG[indicator_id], window)

    return app
