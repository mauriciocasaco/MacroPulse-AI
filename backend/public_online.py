"""Production API over least-privilege views of the shared Supabase data."""

from contextlib import ExitStack
from functools import partial
from os import environ
from time import monotonic

import psycopg

from .macro_data.api import MAX_DAYS, MacroIndicator, indicator_metadata, load_history
from .macro_data.contracts import CATALOG
from .market_data.api_config import _ROLE_CHECK, _WRITE_PRIVILEGES
from .public_demo import (
    SHARED_PROJECT_REF, DemoConfigurationError, DemoSettings, create_public_app, demo_target,
    parse_research, resolve_demo_settings, tls_dsn,
)

ROLE = "macropulse_online_ro"
SCHEMA = "macropulse_online"
VIEWS = tuple(f"{SCHEMA}.{name}" for name in (
    "series", "capture", "observation_revision", "revision_capture", "news_evidence",
))

_SCOPE = """
    SELECT current_user = %s AND NOT r.rolinherit
        AND has_schema_privilege('macropulse_online', 'USAGE')
        AND NOT EXISTS (
            SELECT FROM unnest(%s::text[]) AS allowed(name)
            WHERE NOT EXISTS (
                SELECT FROM pg_class view_relation WHERE view_relation.oid = to_regclass(allowed.name)
                  AND view_relation.relkind = 'v' AND has_table_privilege(view_relation.oid, 'SELECT')
            )
        )
        AND NOT EXISTS (
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.oid <> ALL(%s::regclass[]) AND c.relpersistence <> 't'
              AND (n.nspname NOT IN ('pg_catalog', 'information_schema') OR c.oid >= 16384)
              AND CASE
                WHEN c.relkind = 'S' THEN has_sequence_privilege(c.oid, 'USAGE, SELECT, UPDATE')
                WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f', 't')
                    THEN has_any_column_privilege(c.oid, 'SELECT')
                ELSE false END
              AND NOT (
                  c.relkind = 'v' AND c.relname IN ('pg_stat_statements', 'pg_stat_statements_info')
                  AND EXISTS (
                      SELECT FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
                      WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                        AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                        AND e.extname = 'pg_stat_statements'
                  )
              )
        )
        AND NOT EXISTS (
            SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE has_schema_privilege(n.oid, 'USAGE') AND has_function_privilege(p.oid, 'EXECUTE')
              AND (p.prosecdef OR (n.nspname = 'net' AND p.proname ~ '^(http_|_http_|worker_restart$)'))
        )
        AND NOT EXISTS (
            SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE has_function_privilege(p.oid, 'EXECUTE')
              AND (
                  (n.nspname = 'pg_catalog' AND p.proname IN
                       ('pg_read_file', 'pg_read_binary_file', 'lo_import', 'lo_export'))
                  OR (has_schema_privilege(n.oid, 'USAGE') AND p.oid >= 16384 AND NOT EXISTS (
                      SELECT FROM pg_depend d
                      WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                        AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                  ))
              )
        )
        AND NOT EXISTS (
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
        AND NOT EXISTS (SELECT FROM pg_tablespace WHERE has_tablespace_privilege(oid, 'CREATE'))
        AND NOT EXISTS (SELECT FROM pg_parameter_acl WHERE has_parameter_privilege(parname, 'SET, ALTER SYSTEM'))
        AND NOT has_any_column_privilege('pg_catalog.pg_statistic', 'SELECT')
        AND NOT has_any_column_privilege('pg_catalog.pg_statistic_ext_data', 'SELECT')
        AND NOT has_any_column_privilege('pg_catalog.pg_authid', 'SELECT')
        AND NOT has_any_column_privilege('pg_catalog.pg_shadow', 'SELECT')
        AND NOT has_any_column_privilege('pg_catalog.pg_user_mapping', 'SELECT')
        AND NOT has_any_column_privilege('pg_catalog.pg_largeobject', 'SELECT')
        AND NOT has_column_privilege('pg_catalog.pg_subscription', 'subconninfo', 'SELECT')
        AND current_setting('lo_compat_privileges') = 'off'
        AND NOT EXISTS (
            SELECT FROM pg_largeobject_metadata m CROSS JOIN LATERAL aclexplode(m.lomacl) a
            WHERE m.lomowner <> r.oid AND a.grantee IN (0, r.oid) AND a.privilege_type IN ('SELECT', 'UPDATE')
        )
        AND NOT EXISTS (SELECT FROM pg_foreign_server WHERE has_server_privilege(oid, 'USAGE'))
        AND NOT EXISTS (SELECT FROM pg_foreign_data_wrapper WHERE has_foreign_data_wrapper_privilege(oid, 'USAGE'))
    FROM pg_roles r WHERE r.rolname = current_user
"""


def resolve_online_settings(env) -> DemoSettings:
    # Reuse origin/operational settings without accepting the DEMO data source.
    options = demo_target(
        env.get("MACROPULSE_ONLINE_DSN", ""), SHARED_PROJECT_REF, ROLE,
        shared_project=True, shared_role=ROLE,
    )
    dsn = tls_dsn(options, env.get("MACROPULSE_ONLINE_SSL_ROOT_CERT", ""))
    return resolve_demo_settings(env, verified_dsn=dsn)


def verify_online_role(connection: psycopg.Connection) -> None:
    if (connection.execute(_ROLE_CHECK, (_WRITE_PRIVILEGES,)).fetchone() != (True,)
            or connection.execute(_SCOPE, (ROLE, list(VIEWS), list(VIEWS))).fetchone() != (True,)):
        raise DemoConfigurationError("MacroPulse PostgreSQL role lacks required read-only scope")


def connect_online(settings: DemoSettings) -> psycopg.Connection:
    connection = psycopg.connect(settings.dsn)
    try:
        sslmode = next((option.val for option in connection.pgconn.info if option.keyword == b"sslmode"), None)
        if not connection.pgconn.ssl_in_use or sslmode != b"verify-full":
            raise DemoConfigurationError("MacroPulse PostgreSQL requires TLS verify-full")
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '1s'")
        connection.execute("SET LOCAL transaction_timeout = '20s'")
        connection.execute("SET LOCAL search_path = pg_catalog")
        verify_online_role(connection)
    except Exception:
        connection.close()
        raise
    return connection


def read_online_catalog(connection: psycopg.Connection):
    from psycopg.rows import dict_row

    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute("""
            SELECT s.*, s.indicator_id AS id, least(revisions.first_period, coverage.first_day) AS available_from
            FROM macropulse_online.series s
            LEFT JOIN LATERAL (
                SELECT min(r.period_start) AS first_period
                FROM macropulse_online.observation_revision r
                WHERE r.indicator_id = s.indicator_id
            ) revisions ON true
            LEFT JOIN LATERAL (
                SELECT min(c.requested_start) AS first_day
                FROM macropulse_online.capture c
                WHERE c.indicator_id = s.indicator_id AND c.stale_observations = 0
                  AND c.source_attributes->>'version_scope' IS DISTINCT FROM 'provider_repository'
            ) coverage ON s.frequency = 'effective_date'
        """).fetchall()
    by_id = {row["id"]: row for row in rows}
    if by_id.keys() != CATALOG.keys():
        raise DemoConfigurationError("MacroPulse catalog is incomplete")
    result = []
    for key, item in CATALOG.items():
        metadata = MacroIndicator.model_validate(by_id[key])
        if metadata != indicator_metadata(item):
            raise DemoConfigurationError("MacroPulse catalog differs from the official series contract")
        result.append(metadata.model_dump(mode="json") | {"available_from": by_id[key]["available_from"]})
    return result


def run_online_research(settings, request, key, model, deadline):
    from .macro_ai.online_evidence import retrieve_online
    from .macro_ai.public_gemini import PublicGemini
    from .macro_ai.v2_service import ResearchRuntime, run

    provider = PublicGemini(api_key=key, model=model, deadline=deadline)
    with ExitStack() as stack:
        connection = None

        def reader():
            nonlocal connection
            if monotonic() >= deadline:
                raise TimeoutError
            if connection is None:
                connection = stack.enter_context(connect_online(settings))
            return connection

        def read(indicator, window):
            return load_history(reader(), CATALOG[indicator], window, schema=SCHEMA)

        def retrieve(*arguments):
            return retrieve_online(reader(), *arguments)

        def compose(evidence):
            stack.close()
            return provider.compose(evidence)

        runtime = ResearchRuntime(read, provider.interpret, compose, model,
                                  supplemental_retriever=retrieve,
                                  capture_vintage="latest available Supabase")
        return run(request, runtime=runtime)


def create_online_app():
    return create_public_app(
        settings=resolve_online_settings(environ), connect=connect_online,
        history_reader=partial(load_history, schema=SCHEMA), catalog_reader=read_online_catalog,
        research_runner=run_online_research, research_parser=partial(parse_research, online=True),
        max_window_days=MAX_DAYS,
    )
