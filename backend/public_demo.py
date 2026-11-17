"""Restricted API for the isolated public MacroPulse demonstration."""

from __future__ import annotations

from asyncio import get_running_loop, shield, timeout
from collections.abc import Callable, Mapping
from contextlib import ExitStack, asynccontextmanager
from contextvars import copy_context
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
from .origin_attestation import Attestation, RESEARCH_PATH, consume_grant, origin_config
from .research_logging import research_event, research_id, research_stage

if TYPE_CHECKING:
    from .macro_ai.models import IndicatorId, ResearchRequest

ORIGIN_HEADER = "x-marketpulse-origin"
CLIENT_IP_HEADER = "x-marketpulse-client-ip"
RESEARCH_HEADER = "X-MacroPulse-Public-Research"
RESEARCH_ID_HEADER = "X-MacroPulse-Research-Id"
MAX_WINDOW_DAYS = 2600
MAX_BODY = 4096
MAX_RESEARCH_RESPONSE = 64 * 1024
RESEARCH_SECONDS = 65
RESEARCH_LEASE_SECONDS = 90
LEASE_HEADROOM_SECONDS = 2
LEASE_CLOCK_SKEW_SECONDS = 5
GENERAL_FALLBACK_FLAG = "MACROPULSE_RESEARCH_GENERAL_FALLBACK_ENABLED"
RO_ROLE = "marketpulse_demo_ro"
SHARED_PROJECT_REF = "swpithkjviavycrxpwed"
_MACRO_CATALOG_PATH = "/macro/indicators"
_MACRO_HISTORY_PATHS = frozenset(f"{_MACRO_CATALOG_PATH}/{key}/history" for key in CATALOG)
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


def parse_research(body: bytes, *, online: bool = False, secrets: tuple[str, ...] = ()) -> ResearchRequest:
    from .macro_ai.models import OnlineResearchRequest, PublicResearchRequest, ResearchRequest
    from .macro_ai.research_intent_gate import require_admitted_research
    from .macro_ai.sensitive_input import SensitiveInputError, contains_sensitive_input

    payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(payload, dict) or any(type(value) is not str for value in payload.values()):
        raise ValueError("Invalid Research request")
    question = payload.get("question", "")
    if 5 <= len(question) <= 500:
        if contains_sensitive_input(question, secrets=secrets):
            raise SensitiveInputError
        require_admitted_research(question)
    contract = OnlineResearchRequest if online else PublicResearchRequest
    model = contract.model_validate(payload)
    return ResearchRequest.model_validate(model.model_dump(exclude={"research_version"}))


def _research_config(env: Mapping[str, str]) -> tuple[str, str] | None:
    if env.get("MARKETPULSE_DEMO_RESEARCH_ENABLED") != "true":
        return None
    started = monotonic()
    try:
        from .macro_ai.public_gemini import MODELS
    except ImportError as error:
        research_event("fastapi.research_import", started, type(error).__name__)
        return None

    key = env.get("MARKETPULSE_DEMO_GEMINI_API_KEY", "")
    model = env.get("MARKETPULSE_DEMO_GEMINI_MODEL", "gemini-3.6-flash")
    # AI Studio authorization keys use AQ.; legacy standard keys use AIza.
    valid_key = re.fullmatch(r"(?:AIza[A-Za-z0-9_-]{35,80}|AQ\.[A-Za-z0-9_-]{32,160})", key)
    if not valid_key or model not in MODELS:
        research_event("fastapi.research_config", started, "InvalidResearchConfiguration")
        return None
    return key, model


def run_public_research(settings: DemoSettings, request: ResearchRequest, key: str, model: str, deadline: float):
    from .macro_ai.public_gemini import PublicGemini
    from .macro_ai.v2_service import ResearchRuntime, run

    provider = PublicGemini(api_key=key, model=model, deadline=deadline)
    # All operands share one verified read-only transaction, opened only if needed.
    with ExitStack() as stack:
        connection = None

        def read(indicator: IndicatorId, window: MacroWindow) -> MacroHistory:
            nonlocal connection
            if monotonic() >= deadline:
                raise TimeoutError
            if connection is None:
                with research_stage("supabase.connect"):
                    connection = stack.enter_context(connect_demo(settings))
            with research_stage("supabase.history"):
                if monotonic() >= deadline:
                    raise TimeoutError
                return read_history(connection, CATALOG[indicator], window, max_points=MAX_WINDOW_DAYS)

        def compose(evidence):
            with research_stage("supabase.transaction_close"):
                stack.close()
            return provider.compose(evidence)

        def general(*args, **kwargs):
            with research_stage("supabase.transaction_close"):
                stack.close()
            return provider.general(*args, **kwargs)

        result = run(request, runtime=ResearchRuntime(
            read, provider.interpret, compose, model,
            generate_general=general if os.environ.get(GENERAL_FALLBACK_FLAG) == "true" else None,
        ))
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
    attestation_config = origin_config(os.environ, settings.origin_secret)
    research = _research_config(os.environ) if attestation_config else None
    if research:
        try:
            with research_stage("fastapi.research_preload", startup=True):
                from .macro_ai.v2_retrieval import lexical
                from .macro_ai.v2_service import workflow

                lexical()
                workflow()
        except Exception:
            research = None
    sensitive_values = (
        research[0], conninfo_to_dict(settings.dsn)["password"],
        settings.origin_secret, *attestation_config.sensitive_values,
    ) if research and attestation_config else ()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            with research_stage("supabase.startup_verify", startup=True):
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

    @app.post(RESEARCH_PATH)
    async def respond_to_research(request: Request) -> Response:
        token = research_id.set(request.state.attested_research_id)
        try:
            return await serve_research(request)
        finally:
            research_id.reset(token)

    async def serve_research(request: Request) -> Response:
        from .macro_ai.research_intent_gate import DISALLOWED_CODE, DISALLOWED_MESSAGE, DisallowedResearchError
        from .macro_ai.sensitive_input import (
            SENSITIVE_CODE, SENSITIVE_MESSAGE, SensitiveInputError, configured_sensitive_values,
        )

        started = monotonic()
        try:
            params = research_parser(request.state.research_body, secrets=sensitive_values)
        except (SensitiveInputError, DisallowedResearchError) as error:
            code, message = ((SENSITIVE_CODE, SENSITIVE_MESSAGE) if isinstance(error, SensitiveInputError)
                             else (DISALLOWED_CODE, DISALLOWED_MESSAGE))
            research_event("fastapi.admission", started, type(error).__name__, http_status=422)
            return JSONResponse(
                status_code=422,
                content={"code": code, "detail": message},
                headers={"Cache-Control": "no-store", "X-MacroPulse-Research-Complete": "true"},
            )
        except ValueError:
            response = _error(422, "Invalid request parameters")
            response.headers["X-MacroPulse-Research-Complete"] = "true"
            return response
        if len(research_tasks) >= 2:
            research_event("fastapi.admission", started, "ResearchBusy", http_status=503)
            return _error(503, "Public Research is busy", 1)
        research_event("fastapi.admission", started)
        deadline = started + RESEARCH_SECONDS

        def execute():
            with research_stage("fastapi.research_runner"):
                if monotonic() >= deadline:
                    raise TimeoutError
                token = configured_sensitive_values.set(sensitive_values)
                try:
                    return (research_runner or run_public_research)(settings, params, *research, deadline)
                finally:
                    configured_sensitive_values.reset(token)

        task = get_running_loop().run_in_executor(None, copy_context().run, execute)
        research_tasks.add(task)

        def settled(finished):
            research_tasks.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(settled)
        # A cancelled HTTP waiter cannot stop an already running thread. Keep
        # its slot until the executor actually settles and omit the completion
        # header on timeouts so the Worker retains the lease.
        try:
            async with timeout(deadline - monotonic()):
                result = await shield(task)
            with research_stage("fastapi.serialize"):
                if monotonic() >= deadline:
                    raise TimeoutError
                payload = result.model_dump_json().encode("utf-8")
                if monotonic() >= deadline:
                    raise TimeoutError
                if len(payload) > MAX_RESEARCH_RESPONSE:
                    research_event("fastapi.response", started, "ResponseTooLarge", http_status=503)
                    return _error(503, "Public Research is unavailable")
            research_event("fastapi.response", started, http_status=200)
            return Response(payload, media_type="application/json", headers={
                "Cache-Control": "no-store", "X-MacroPulse-Research-Complete": "true",
            })
        except Exception as error:
            deadline_reached = monotonic() >= deadline
            if isinstance(error, TimeoutError) or deadline_reached:
                if deadline_reached:
                    research_event("fastapi.deadline", started, "TimeoutError", http_status=504)
                research_event("fastapi.response", started, "TimeoutError", http_status=504)
                return _error(504, "Public Research timed out")
            research_event("fastapi.response", started, type(error).__name__, http_status=503)
            return _error(503, "Public Research is unavailable")

    @app.middleware("http")
    async def restrict(request: Request, call_next):
        nonlocal read_in_flight
        path = request.scope["path"]
        analysis = path == RESEARCH_PATH
        if analysis:
            if request.headers.getlist(ORIGIN_HEADER):
                return _error(403, "Forbidden")
            try:
                attestation = Attestation.from_request(request)
            except ValueError:
                return _error(403, "Forbidden")
        else:
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

        raw_path = request.scope.get("raw_path", b"")
        if raw_path != path.encode("utf-8") or len(request.scope["query_string"]) > 256:
            return _error(400, "Invalid request")
        operation = "catalog" if path == _MACRO_CATALOG_PATH else "history" if path in _MACRO_HISTORY_PATHS else None
        if not (operation or analysis):
            return _error(404, "Not found")
        if request.method != ("POST" if analysis else "GET") and not (
            operation == "catalog" and request.method == "HEAD"
        ):
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
        if analysis and (types != ["application/json"] or request.headers.getlist("content-encoding")):
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
            if attestation_config is None:
                return _error(503, "Public Research is unavailable")
            commitment = attestation.authenticate(attestation_config, body, int(time() * 1000))
            if commitment is None:
                return _error(403, "Forbidden")
            if research is None:
                token = research_id.set(attestation.research_id)
                try:
                    research_event("fastapi.admission", monotonic(), "ResearchUnavailable", http_status=503)
                finally:
                    research_id.reset(token)
                return _error(503, "Public Research is unavailable")
            remaining = attestation.expires / 1000 - time()
            if not RESEARCH_SECONDS + LEASE_HEADROOM_SECONDS < remaining <= (
                RESEARCH_LEASE_SECONDS + LEASE_CLOCK_SKEW_SECONDS
            ):
                return _error(403, "Forbidden")
            started = monotonic()
            token = research_id.set(attestation.research_id)
            try:
                consumed = await consume_grant(attestation_config, attestation, commitment)
                if consumed is None:
                    research_event("fastapi.origin_consume", started, "OriginConsumeUnavailable", http_status=503)
                    return _error(503, "Public Research is unavailable")
                if not consumed or attestation.expires / 1000 - time() <= RESEARCH_SECONDS + LEASE_HEADROOM_SECONDS:
                    research_event("fastapi.origin_consume", started, "OriginConsumeRejected", http_status=403)
                    return _error(403, "Forbidden")
                research_event("fastapi.origin_consume", started, http_status=204)
            finally:
                research_id.reset(token)
            request.state.attested_research_id = attestation.research_id
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
        if response.status_code == 200:
            # Explicit internal contract: only allowlisted, authenticated public reads.
            # The Worker may cache the payload, but browsers must still use no-store.
            response.headers["X-MacroPulse-Public-Cache"] = "official-v1"
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

    @app.head(_MACRO_CATALOG_PATH)
    def macro_catalog_status():
        # Cache hits must recalculate Research availability without opening a database connection.
        return Response(media_type="application/json", headers={
            **({RESEARCH_HEADER: "true"} if research is not None else {}),
        })

    @app.get(f"{_MACRO_CATALOG_PATH}/{{indicator_id}}/history")
    def macro_history(indicator_id: str, window: Annotated[MacroWindow, Query()]) -> MacroHistory:
        if (window.end - window.start).days > max_window_days:
            raise HTTPException(status_code=422, detail="Invalid request parameters")
        with open_reader() as connection:
            return history_reader(connection, CATALOG[indicator_id], window)

    return app
