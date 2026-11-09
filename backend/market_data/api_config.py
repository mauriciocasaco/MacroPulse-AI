"""Resolve the market API credential and enforce the local Supabase DEV boundary."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys

from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from starlette.types import Scope

from .connection import secure_supabase_dsn
from .dev_import import DEV_PROJECT_REF, DevImportError, connect_dev, dev_dsn


_ROOT = Path(__file__).resolve().parents[2]
_ROUTE_OVERRIDES = ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS")
_DEV_REFERENCE = f".{DEV_PROJECT_REF}"
_RO_OPTIONS = {
    "USER_RO": "user",
    "PASS_USER_RO": "password",
    "HOST_RO": "host",
    "PORT_RO": "port",
    "DB_RO": "dbname",
    "SSL_MODE_RO": "sslmode",
    "SSL_ROOT_CERT_RO": "sslrootcert",
}


class MarketApiConfigError(RuntimeError):
    """Only fixed, non-sensitive reason codes are passed to this exception."""


@dataclass(frozen=True)
class MarketApiConfig:
    dsn: str = field(repr=False)
    dev: bool = False


def _local_uvicorn(scope: Scope | None) -> bool:
    # A loopback peer alone is insufficient: it can reach a wildcard listener.
    # Accept only the documented launch shape and rule out inherited sockets.
    match sys.orig_argv[1:]:
        case ["-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", port]:
            if not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
                return False
        case _:
            return False
    if any(os.environ.get(key) for key in ("UVICORN_FD", "UVICORN_UDS", "UVICORN_ENV_FILE")):
        return False
    if scope is None or tuple(scope.get("server") or ()) != ("127.0.0.1", int(port)):
        return False
    peer = scope.get("client")
    if not peer or peer[0] != "127.0.0.1":
        return False
    headers = scope.get("headers", ())
    if any(name == b"forwarded" or name.startswith(b"x-forwarded-") for name, _ in headers):
        return False
    hosts = [value for name, value in headers if name == b"host"]
    return len(hosts) == 1 and bool(re.fullmatch(rb"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", hosts[0]))


def _read_dotenv(name: str) -> dict[str, str | None]:
    try:
        return dotenv_values(_ROOT / name, encoding="utf-8-sig", interpolate=False)
    except (OSError, ValueError):
        raise MarketApiConfigError("local_dotenv_unreadable") from None


def _project_ref(local: dict[str, str | None], shared: dict[str, str | None]) -> str | None:
    key = "MARKETPULSE_DEV_PROJECT_REF"
    return os.environ.get(key, local.get(key, shared.get(key)))


def is_dev_target(host: str, user: str) -> bool:
    return DEV_PROJECT_REF in host.lower() or user.lower().endswith(_DEV_REFERENCE)


def _dev_config(project: str | None, dsn: str | None) -> MarketApiConfig:
    if project != DEV_PROJECT_REF:
        raise MarketApiConfigError("dev_project_mismatch")
    if not dsn:
        raise MarketApiConfigError("dev_dsn_missing")
    if any(os.environ.get(key) for key in _ROUTE_OVERRIDES):
        raise MarketApiConfigError("dev_libpq_override")
    try:
        options = conninfo_to_dict(dsn)
        pinned_options = conninfo_to_dict(secure_supabase_dsn(""))
        # Previously normalized API DSNs may include these same pinned settings.
        for key, expected in (("sslrootcert", pinned_options["sslrootcert"]), ("gssencmode", "disable"), ("connect_timeout", "8")):
            if options.pop(key, expected) != expected:
                raise DevImportError("unexpected DEV connection option")
        return MarketApiConfig(dev_dsn(project, make_conninfo(**options)), dev=True)
    except (DevImportError, OSError, ValueError, psycopg.Error):
        raise MarketApiConfigError("dev_route_or_tls_config_invalid") from None


def _split_dev_config(shared: dict[str, str | None]) -> MarketApiConfig:
    options = {option: os.environ.get(key, shared.get(key)) for key, option in _RO_OPTIONS.items()}
    if not all(options.values()):
        raise MarketApiConfigError("dev_ro_settings_incomplete")
    expected_user = "marketpulse_api_ro"
    if options["host"] != f"db.{DEV_PROJECT_REF}.supabase.co":
        expected_user += _DEV_REFERENCE
    if options["user"] != expected_user or options["sslmode"] != "verify-full":
        raise MarketApiConfigError("dev_ro_user_or_tls_invalid")
    try:
        ca = (_ROOT / options["sslrootcert"]).resolve()
        pinned = Path(conninfo_to_dict(secure_supabase_dsn(""))["sslrootcert"]).resolve()
        if ca != pinned:
            raise MarketApiConfigError("dev_ro_ca_invalid")
        options.pop("sslrootcert")  # The DEV validator supplies the pinned spelling.
        dsn = make_conninfo(**options)
    except (OSError, ValueError, psycopg.Error):
        raise MarketApiConfigError("dev_route_or_tls_config_invalid") from None
    return _dev_config(_project_ref({}, shared), dsn)


def resolve_market_api(scope: Scope | None) -> MarketApiConfig:
    """Honor explicit settings; use ignored dotenv files only on local startup."""
    explicit = os.environ.get("MARKET_API_DSN")
    if explicit is not None:
        if not explicit:
            raise MarketApiConfigError("process_dsn_empty")
        try:
            options = conninfo_to_dict(explicit)
        except (ValueError, psycopg.Error):
            raise MarketApiConfigError("process_dsn_invalid") from None
        host = options.get("host", os.environ.get("PGHOST", ""))
        user = options.get("user", os.environ.get("PGUSER", ""))
        if is_dev_target(host, user):
            if not _local_uvicorn(scope):
                raise MarketApiConfigError("local_dev_launch_required")
            # This process credential still needs to identify the approved DEV project.
            project = os.environ.get("MARKETPULSE_DEV_PROJECT_REF")
            if project is None and not os.environ.get("MARKETPULSE_DISABLE_LOCAL_DOTENV"):
                project = _project_ref(_read_dotenv(".env.market-api"), _read_dotenv(".env"))
            return _dev_config(project, explicit)
        return MarketApiConfig(make_conninfo(explicit, sslmode="verify-full", gssencmode="disable", connect_timeout=5))

    if os.environ.get("MARKETPULSE_DISABLE_LOCAL_DOTENV") or not _local_uvicorn(scope):
        raise MarketApiConfigError("local_dotenv_disabled")
    shared = _read_dotenv(".env")
    if any(key in os.environ or key in shared for key in _RO_OPTIONS):
        return _split_dev_config(shared)
    if (_ROOT / ".env.market-api").exists():
        local = _read_dotenv(".env.market-api")
        return _dev_config(_project_ref(local, shared), local.get("MARKET_API_DSN"))
    return _dev_config(_project_ref({}, shared), shared.get("MARKET_API_DSN"))


_WRITE_PRIVILEGES = "INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, SELECT WITH GRANT OPTION"
_REQUIRED_SCHEMAS = ("market_data", "news", "extensions")
_REQUIRED_TABLES = (
    "market_data.assets", "market_data.asset_sources", "market_data.data_sources", "market_data.ingestion_runs",
    "market_data.price_bars", "news.sources", "news.articles", "news.article_records", "news.record_assets",
    "news.record_embeddings",
)

_ROLE_CHECK = """
    WITH user_schemas AS (
        SELECT oid, nspowner FROM pg_namespace
        WHERE NOT starts_with(nspname, 'pg_') AND nspname <> 'information_schema'
    )
    SELECT current_user = session_user AND r.rolcanlogin
           AND current_setting('transaction_read_only') = 'on'
           AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
           AND NOT has_database_privilege(current_database(), 'CREATE')
           AND NOT EXISTS (SELECT 1 FROM pg_database d WHERE d.datname = current_database() AND d.datdba = r.oid)
           AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid)
           AND NOT EXISTS (
               SELECT 1 FROM user_schemas n WHERE n.nspowner = r.oid OR has_schema_privilege(n.oid, 'CREATE')
           )
           AND NOT EXISTS (
               SELECT 1 FROM pg_class c JOIN user_schemas n ON n.oid = c.relnamespace
               WHERE c.relowner = r.oid OR (has_schema_privilege(n.oid, 'USAGE') AND CASE
                   WHEN c.relkind = 'S' THEN has_sequence_privilege(c.oid, 'USAGE, UPDATE')
                   WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
                       has_table_privilege(c.oid, %s)
                       OR has_any_column_privilege(c.oid, 'INSERT, UPDATE, REFERENCES, SELECT WITH GRANT OPTION')
                   ELSE FALSE END)
           )
           AND NOT EXISTS (
               SELECT 1 FROM pg_proc p JOIN user_schemas n ON n.oid = p.pronamespace
               WHERE p.proowner = r.oid OR (p.prosecdef
                   AND p.prorettype NOT IN ('pg_catalog.trigger'::regtype, 'pg_catalog.event_trigger'::regtype)
                   AND has_schema_privilege(n.oid, 'USAGE') AND has_function_privilege(p.oid, 'EXECUTE'))
           )
           AND NOT EXISTS (
               SELECT 1 FROM pg_type t JOIN user_schemas n ON n.oid = t.typnamespace WHERE t.typowner = r.oid
           )
    FROM pg_roles r WHERE r.rolname = current_user
"""

_READ_PRIVILEGES = """
    SELECT COALESCE(has_type_privilege(to_regtype('extensions.vector'), 'USAGE'), FALSE)
           AND NOT EXISTS (
               SELECT 1 FROM unnest(%s::text[]) AS s(name)
               WHERE NOT COALESCE(has_schema_privilege(to_regnamespace(s.name), 'USAGE'), FALSE)
           )
           AND NOT EXISTS (
               SELECT 1 FROM unnest(%s::text[]) AS t(name)
               WHERE NOT COALESCE(has_table_privilege(to_regclass(t.name), 'SELECT'), FALSE)
           )
"""


def connect_read_only_dev(dsn: str) -> psycopg.Connection:
    """Verify each new connection before exposing it to a repository or retrieval."""
    try:
        connection = connect_dev(dsn)
    except (DevImportError, ValueError, psycopg.Error):
        raise MarketApiConfigError("dev_verified_connection_failed") from None

    try:
        connection.read_only = True
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL search_path = pg_catalog")
        if connection.execute(_ROLE_CHECK, (_WRITE_PRIVILEGES,)).fetchone() != (True,):
            raise MarketApiConfigError("dev_credential_not_read_only")
        if connection.execute(_READ_PRIVILEGES, (list(_REQUIRED_SCHEMAS), list(_REQUIRED_TABLES))).fetchone() != (True,):
            raise MarketApiConfigError("dev_credential_missing_select")
    except MarketApiConfigError:
        connection.close()
        raise
    except psycopg.Error:
        connection.close()
        raise MarketApiConfigError("dev_read_only_verification_failed") from None
    return connection
