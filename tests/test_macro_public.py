from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from backend import public_demo as demo
from backend.macro_data.api import MacroWindow
from backend.macro_data.contracts import CATALOG
from backend.macro_data.public import ProjectionError, read_history, stamp_projection, verify_projection


SECRET = sha256(b"synthetic macro DEMO origin fixture").hexdigest()
HEADERS = {demo.ORIGIN_HEADER: SECRET, demo.CLIENT_IP_HEADER: "192.0.2.30"}
CATALOG_PATH = "/macro/indicators"
HISTORY_PATH = CATALOG_PATH + "/deposit-rate/history"
WINDOW = "?start=2024-02-01&end=2024-02-10"


def _setup_settings(monkeypatch, tmp_path, **overrides):
    ca = tmp_path / "synthetic-demo-ca.pem"
    ca.write_text("Fixture CA path; no connection is opened")
    fixture_password = sha256(b"synthetic macro projection database fixture").hexdigest()
    settings = {
        "MARKETPULSE_DEMO_PROJECT_REF": "abcdefghijklmnopqrst",
        "MARKETPULSE_DEMO_DSN": (
            "host=db.abcdefghijklmnopqrst.supabase.co port=5432 dbname=postgres "
            f"user=marketpulse_demo_ro password={fixture_password}"
        ),
        "MARKETPULSE_DEMO_ORIGIN_SECRET": SECRET,
        "MARKETPULSE_DEMO_SSL_ROOT_CERT": str(ca),
        **overrides,
    }
    monkeypatch.setattr(demo.os, "environ", settings)
    return settings


def test_projection_required_and_config_fails_closed(monkeypatch, tmp_path):
    settings = _setup_settings(monkeypatch, tmp_path)
    flag = "MARKETPULSE_DEMO_MACRO_ENABLED"
    with pytest.raises(demo.DemoConfigurationError, match="requires the macro projection"):
        demo.resolve_demo_settings({**settings, flag: "false"})
    for key in ("MACRO_API_DSN", "DATABASE_URL", "MARKETPULSE_DEV_PROJECT_REF", "PASS_USER_RO", "GEMINI_API_KEY"):
        with pytest.raises(demo.DemoConfigurationError, match="dedicated"):
            demo.resolve_demo_settings({**settings, flag: "true", key: "synthetic not-for-DEMO fixture"})


def test_macro_enabled_startup_requires_verified_projection(monkeypatch, tmp_path):
    _setup_settings(monkeypatch, tmp_path, MARKETPULSE_DEMO_MACRO_ENABLED="true")

    def invalid(_dsn):
        raise ProjectionError("synthetic invalid projection")

    monkeypatch.setattr(demo, "connect_demo", invalid)
    with pytest.raises(demo.DemoConfigurationError, match="startup verification"):
        with TestClient(demo.create_public_app()):
            pass


def test_macro_opt_in_protects_routes_dates_origin_quota_and_methods(monkeypatch, tmp_path):
    _setup_settings(monkeypatch, tmp_path, MARKETPULSE_DEMO_MACRO_ENABLED="true",
                    MARKETPULSE_DEMO_CATALOG_PER_MINUTE="1")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": {
            "captured_at": datetime(2024, 2, 8, tzinfo=timezone.utc),
            "captured_on": date(2024, 2, 8), "today": date(2024, 2, 10),
        }}),
        MagicMock(**{"fetchone.return_value": {"first_day": date(2024, 2, 1), "last_day": date(2024, 2, 8)}}),
        MagicMock(**{"fetchall.return_value": []}),
    ]

    def connect(_dsn):
        return connection

    monkeypatch.setattr(demo, "connect_demo", connect)
    with TestClient(demo.create_public_app()) as client:
        assert client.get(CATALOG_PATH).status_code == 403
        assert client.get(HISTORY_PATH + WINDOW, headers={demo.ORIGIN_HEADER: SECRET}).status_code == 403
        assert client.post(CATALOG_PATH, headers=HEADERS).status_code == 405
        assert client.get(CATALOG_PATH + "?unknown=fixture", headers=HEADERS).status_code == 400
        assert client.get(HISTORY_PATH + WINDOW + "&start=2024-02-01", headers=HEADERS).status_code == 400
        assert client.get(CATALOG_PATH + "/unapproved/history" + WINDOW, headers=HEADERS).status_code == 404
        start = date(2017, 1, 1)
        end = start + timedelta(days=2600)
        assert client.get(HISTORY_PATH + f"?start={start}&end={end}", headers=HEADERS).status_code == 422
        malformed = client.get(HISTORY_PATH + "?start=2024-01-01&end=fixture-input", headers=HEADERS)
        assert malformed.status_code == 422 and malformed.json() == {"detail": "Invalid request parameters"}
        result = client.get(CATALOG_PATH, headers=HEADERS)
        assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
        assert [item["id"] for item in result.json()] == list(CATALOG)
        assert client.get(CATALOG_PATH, headers=HEADERS).status_code == 429
        result = client.get(HISTORY_PATH + WINDOW, headers=HEADERS)
        assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
        assert result.json()["coverage_end"] == "2024-02-08"


def _load_synthetic_projection(db):
    for item in CATALOG.values():
        coverage = "{[2024-01-01,2024-02-06),[2024-02-10,2024-02-13)}" if item.id == "deposit-rate" else None
        db.execute("""
            INSERT INTO marketpulse_demo.macro_catalog
              (indicator_id, series_key, captured_at, requested_start, requested_end, response_sha256,
               bundle_sha256, deposit_coverage, projected_rows, projection_sha256)
            VALUES (%s, %s, '2024-04-01T10:30:00+00:00', '2024-01-01', '2024-04-01', repeat('a', 64),
                    repeat('b', 64), %s, %s, repeat('0', 64))
        """, (item.id, item.series_key, coverage, 2 if item.id in {"deposit-rate", "hicp-yoy"} else 1))
    db.execute("""
        INSERT INTO marketpulse_demo.macro_observations VALUES
        ('eur-usd', '2024-02-01', '2024-02-01', '2024-02-01', NULL, 1.2340, 'normal', 'A'),
        ('m3-stock', '2024-01', '2024-01-01', '2024-01-31', NULL, 12000000, 'provisional', 'P'),
        ('real-gdp-qoq', '2024-Q1', '2024-01-01', '2024-03-31', NULL, 0.4, 'normal', NULL),
        ('hicp-yoy', '2024-01', '2024-01-01', '2024-01-31', NULL, 2.1, 'normal', NULL),
        ('hicp-yoy', '2024-02', '2024-02-01', '2024-02-29', NULL, NULL, 'withheld', NULL),
        ('deposit-rate', '2024-01-03', '2024-01-03', '2024-01-03', '2024-01-03', 2.0, 'normal', 'A'),
        ('deposit-rate', '2024-02-10', '2024-02-10', '2024-02-10', '2024-02-10', 2.25, 'normal', 'A')
    """)
    stamp_projection(db)


def test_projection_sql_exact_catalog_and_restricted_reader(shared_demo_database, install_demo):
    with shared_demo_database() as (dsn, admin), psycopg.connect(dsn, row_factory=dict_row) as db:
        install_demo(admin)
        _load_synthetic_projection(db)
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        assert db.execute("SELECT count(*) AS count FROM marketpulse_demo.macro_catalog").fetchone() == {"count": 5}
        assert db.execute("SELECT count(*) AS count FROM marketpulse_demo.macro_observations").fetchone() == {"count": 7}
        db.execute("SET LOCAL TIME ZONE 'Pacific/Auckland'")
        db.execute("SET LOCAL DateStyle TO 'SQL, DMY'")
        verify_projection(db)
        db.execute("SET LOCAL DateStyle TO 'ISO, YMD'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute("DELETE FROM marketpulse_demo.macro_observations WHERE false")
        rate = read_history(
            db, CATALOG["deposit-rate"], MacroWindow(start=date(2024, 2, 1), end=date(2024, 2, 20)), max_points=2600,
        )
        assert rate.anchor.period_raw == "2024-01-03" and rate.coverage_end == date(2024, 2, 5)
        assert [row.period_raw for row in rate.observations] == ["2024-02-10"]
        hicp = read_history(
            db, CATALOG["hicp-yoy"], MacroWindow(start=date(2024, 1, 1), end=date(2024, 2, 29)), max_points=2600,
        )
        assert [(row.period_raw, row.status_kind) for row in hicp.observations] == [
            ("2024-01", "normal"), ("2024-02", "withheld"),
        ]
        assert hicp.observations[1].value is None
        db.execute("RESET ROLE")
        for change in (
            "DELETE FROM marketpulse_demo.macro_observations WHERE indicator_id = 'deposit-rate' "
            "AND period_start = '2024-02-10'",
            "UPDATE marketpulse_demo.macro_observations SET value_decimal = 9 WHERE indicator_id = 'hicp-yoy' "
            "AND period_start = '2024-01-01'",
            "UPDATE marketpulse_demo.macro_catalog SET deposit_coverage = '{[2024-01-01,2024-03-01)}' "
            "WHERE indicator_id = 'deposit-rate'",
        ):
            with pytest.raises(ProjectionError, match="integrity check failed"), db.transaction():
                db.execute(change)
                db.execute("SET LOCAL ROLE marketpulse_demo_ro")
                verify_projection(db)
        db.execute("ALTER TABLE marketpulse_demo.macro_observations ADD COLUMN unexpected text")
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        with pytest.raises(ProjectionError, match="unexpected columns"):
            verify_projection(db)
        db.execute("RESET ROLE")
        db.execute("ALTER TABLE marketpulse_demo.macro_observations DROP COLUMN unexpected")
        db.execute("CREATE TABLE marketpulse_demo.unrelated (value text)")
        db.execute("GRANT SELECT (value) ON marketpulse_demo.unrelated TO marketpulse_demo_ro")
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        with pytest.raises(ProjectionError, match="read scope"):
            verify_projection(db)
        db.execute("RESET ROLE")
        # The Phase 8.1 runtime guard is still used by this checkout.
        db.execute("CREATE SCHEMA macro_data")
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        with pytest.raises(ProjectionError, match="unavailable"):
            verify_projection(db)
        db.rollback()


def test_shared_database_enforces_effective_role_and_privileges(shared_demo_database, install_demo, tmp_path):
    with shared_demo_database() as (dsn, owner):
        if not owner.pgconn.ssl_in_use:
            pytest.skip("requires TLS disposable PostgreSQL fixture")
        owner.execute("""
            CREATE SCHEMA macro_data;
            CREATE SCHEMA market_data;
            CREATE SCHEMA news;
            CREATE SCHEMA vault;
            CREATE TABLE macro_data.fixture_private AS SELECT 'synthetic private row' AS value;
            CREATE TABLE public.fixture_private AS SELECT 'synthetic private row' AS value;
            CREATE EXTENSION pgcrypto;
            CREATE EXTENSION pg_stat_statements;
            CREATE ROLE marketpulse_demo_ro NOINHERIT;
            GRANT USAGE ON SCHEMA public TO marketpulse_demo_ro;
        """)
        owner.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO marketpulse_demo_ro").format(
            sql.Identifier(owner.info.dbname)
        ))
        install_demo(owner)
        with owner.transaction():
            _load_synthetic_projection(owner)
            owner.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        _check_shared_database(owner, dsn, tmp_path)


def _check_shared_database(owner, dsn, tmp_path):
    role_dsn = make_conninfo(dsn, user="marketpulse_demo_ro", gssencmode="disable")
    separate = demo.DemoSettings(role_dsn, SECRET)
    shared = demo.DemoSettings(role_dsn, SECRET, shared_project=True)
    owner.execute("CREATE SEQUENCE marketpulse_demo.private_counter")
    with pytest.raises(demo.DemoConfigurationError, match="required read-only scope"):
        demo.connect_demo(separate)
    with demo.connect_demo(shared) as reader:
        assert reader.execute("""
            SELECT current_user = session_user AND session_user = 'marketpulse_demo_ro'
                AND current_setting('transaction_read_only') = 'on' AS verified
        """).fetchone() == {"verified": True}
        history = read_history(
            reader, CATALOG["hicp-yoy"], MacroWindow(start=date(2024, 1, 1), end=date(2024, 2, 29)), max_points=2600,
        )
        assert [row.period_raw for row in history.observations] == ["2024-01", "2024-02"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege), reader.transaction():
            reader.execute("SELECT * FROM macro_data.fixture_private")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction), reader.transaction():
            reader.execute("DELETE FROM marketpulse_demo.macro_catalog WHERE false")

    with psycopg.connect(role_dsn, autocommit=True) as reader, reader.transaction(force_rollback=True):
        reader.execute("SET TRANSACTION READ WRITE")
        reader.execute("CREATE TEMP TABLE fixture_local AS SELECT 1 AS value")
        assert reader.execute("SELECT value FROM fixture_local").fetchone() == (1,)
        assert reader.execute("SELECT public.digest('fixture', 'sha256') IS NOT NULL").fetchone() == (True,)
        (owned_large_object,) = reader.execute("SELECT lo_from_bytea(0, 'synthetic'::bytea)").fetchone()
        assert reader.execute("SELECT lo_get(%s)", (owned_large_object,)).fetchone() == (b"synthetic",)
        for statement in (
            "DELETE FROM marketpulse_demo.macro_catalog WHERE false",
            "DELETE FROM marketpulse_demo.macro_observations WHERE false",
            "SELECT * FROM public.fixture_private",
            "UPDATE macro_data.fixture_private SET value = 'changed' WHERE false",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), reader.transaction():
                reader.execute(statement)
    bad_identity = demo.DemoSettings(make_conninfo(dsn, options="-c role=marketpulse_demo_ro"), SECRET,
                                    shared_project=True)
    with pytest.raises(demo.DemoConfigurationError, match="required read-only scope"):
        demo.connect_demo(bad_identity)

    mutations = [
        ("GRANT CREATE ON SCHEMA information_schema TO PUBLIC", "REVOKE CREATE ON SCHEMA information_schema FROM PUBLIC"),
        ("GRANT SELECT ON SEQUENCE marketpulse_demo.private_counter TO PUBLIC",
         "REVOKE SELECT ON SEQUENCE marketpulse_demo.private_counter FROM PUBLIC"),
        ("GRANT SET ON PARAMETER lo_compat_privileges TO PUBLIC",
         "REVOKE SET ON PARAMETER lo_compat_privileges FROM PUBLIC"),
        ("GRANT ALTER SYSTEM ON PARAMETER log_min_duration_statement TO PUBLIC",
         "REVOKE ALTER SYSTEM ON PARAMETER log_min_duration_statement FROM PUBLIC"),
        ("GRANT USAGE ON SCHEMA vault TO PUBLIC; CREATE FUNCTION vault.private_read() RETURNS integer "
         "LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
         "DROP FUNCTION vault.private_read(); REVOKE USAGE ON SCHEMA vault FROM PUBLIC"),
        ("GRANT SELECT (value) ON public.fixture_private TO PUBLIC",
         "REVOKE SELECT (value) ON public.fixture_private FROM PUBLIC"),
        ("GRANT SELECT ON macro_data.fixture_private TO PUBLIC",
         "REVOKE SELECT ON macro_data.fixture_private FROM PUBLIC"),
        ("GRANT SELECT ON pg_catalog.pg_statistic TO PUBLIC",
         "REVOKE SELECT ON pg_catalog.pg_statistic FROM PUBLIC"),
        ("CREATE SCHEMA net; CREATE TABLE net.http_request_queue (secret text); "
         "GRANT SELECT (secret) ON net.http_request_queue TO PUBLIC", "DROP SCHEMA net CASCADE"),
        ("CREATE FUNCTION macro_data.private_callback(integer) RETURNS boolean LANGUAGE sql AS 'SELECT true'; "
         "CREATE DOMAIN macro_data.private_domain AS integer CHECK (macro_data.private_callback(VALUE))",
         "DROP DOMAIN macro_data.private_domain; DROP FUNCTION macro_data.private_callback(integer)"),
        ("GRANT USAGE ON SCHEMA news TO marketpulse_demo_ro", "REVOKE USAGE ON SCHEMA news FROM marketpulse_demo_ro"),
        ("GRANT SELECT ON macro_data.fixture_private TO marketpulse_demo_ro",
         "REVOKE SELECT ON macro_data.fixture_private FROM marketpulse_demo_ro"),
        ("GRANT UPDATE ON marketpulse_demo.macro_observations TO marketpulse_demo_ro",
         "REVOKE UPDATE ON marketpulse_demo.macro_observations FROM marketpulse_demo_ro"),
        ("GRANT SELECT (indicator_id) ON marketpulse_demo.macro_observations TO marketpulse_demo_ro",
         "REVOKE SELECT (indicator_id) ON marketpulse_demo.macro_observations FROM marketpulse_demo_ro"),
        ("GRANT pg_read_all_data TO marketpulse_demo_ro", "REVOKE pg_read_all_data FROM marketpulse_demo_ro"),
        ("ALTER ROLE marketpulse_demo_ro BYPASSRLS", "ALTER ROLE marketpulse_demo_ro NOBYPASSRLS"),
        ("GRANT EXECUTE ON FUNCTION pg_read_file(text) TO PUBLIC",
         "REVOKE EXECUTE ON FUNCTION pg_read_file(text) FROM PUBLIC"),
        ("CREATE FUNCTION pg_catalog.fixture_private() RETURNS text LANGUAGE sql AS 'SELECT ''synthetic private row'''",
         "DROP FUNCTION pg_catalog.fixture_private()"),
    ]
    for grant, revoke in mutations:
        owner.execute(grant)
        try:
            with pytest.raises((ProjectionError, demo.DemoConfigurationError), match="privileges|read-only scope"):
                demo.connect_demo(shared)
        finally:
            owner.execute(revoke)
        with demo.connect_demo(shared):
            pass

    large_object = owner.execute("SELECT lo_from_bytea(0, %s)", (b"synthetic private data",)).fetchone()[0]
    owner.execute(sql.SQL("GRANT SELECT ON LARGE OBJECT {} TO PUBLIC").format(sql.Literal(large_object)))
    with pytest.raises(ProjectionError, match="unexpected shared database privileges"):
        demo.connect_demo(shared)
    owner.execute("SELECT lo_unlink(%s)", (large_object,))

    unverified_tls = demo.DemoSettings(make_conninfo(role_dsn, sslmode="require"), SECRET, shared_project=True)
    with pytest.raises(demo.DemoConfigurationError, match="requires TLS verify-full"):
        demo.connect_demo(unverified_tls)

    wrong_host = demo.DemoSettings(
        make_conninfo(role_dsn, host="wrong.fixture.invalid", sslmode="verify-full"), SECRET, shared_project=True,
    )
    with pytest.raises(psycopg.OperationalError, match="certificate.*does not match"):
        demo.connect_demo(wrong_host)
    bad_ca = tmp_path / "invalid-ca.pem"
    bad_ca.write_text("synthetic invalid CA file", encoding="utf-8")
    wrong_ca = demo.DemoSettings(make_conninfo(role_dsn, sslrootcert=str(bad_ca)), SECRET, shared_project=True)
    with pytest.raises(psycopg.OperationalError, match="root certificate file"):
        demo.connect_demo(wrong_ca)

    owner.execute("UPDATE marketpulse_demo.macro_observations SET value_decimal = 9 WHERE value_decimal IS NOT NULL")
    with pytest.raises(ProjectionError, match="integrity check failed"):
        demo.connect_demo(shared)
