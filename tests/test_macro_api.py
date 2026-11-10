from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend.api import app
from backend import public_demo
from backend.macro_data import api as macro
from backend.macro_data.contracts import CATALOG, Observation, parse_period
from backend.macro_data.repository import Capture, record_capture, register_catalog
from backend.market_data.connection import secure_supabase_dsn
from backend.market_data.dev_import import DEV_PROJECT_REF


def _sample(indicator, period, value, *, confidentiality="F", state="normal", flag="A"):
    return Observation(
        parse_period(indicator.frequency, period), Decimal(value) if value is not None else None,
        value, flag, state, confidentiality,
    )


def _store(db, key, start, end, captured, rows=(), *, history=False):
    indicator = CATALOG[key]
    capture = Capture(
        uuid4(), date.fromisoformat(start), date.fromisoformat(end), datetime.fromisoformat(captured),
        indicator.source_url, 200, 1, b"synthetic M3 API fixture",
    )
    attrs = {"version_scope": "provider_repository"} if history else {}
    record_capture(db, indicator, capture, rows, attrs)


@pytest.fixture(scope="module")
def api_dsn(disposable_database):
    with disposable_database("MACRO_DATA_TEST_DSN") as dsn:
        with psycopg.connect(dsn) as db:
            with db.transaction():
                migration = Path("supabase/migrations/202611090001_macro_data.sql")
                db.execute(migration.read_text(encoding="utf-8"))
            register_catalog(db)
            fx, dfr, gdp = (CATALOG[key] for key in ("eur-usd", "deposit-rate", "real-gdp-qoq"))
            _store(db, fx.id, "2024-01-02", "2024-01-04", "2024-01-05T10:30:00+00:00", (
                _sample(fx, "2024-01-02", "1.2300"),
                _sample(fx, "2024-01-04", "987654.321", confidentiality="C"),
            ))
            _store(db, gdp.id, "2024-01-01", "2024-06-30", "2024-07-01T10:30:00+00:00", (
                _sample(gdp, "2024-Q1", "-0.4", confidentiality=None, flag=None),
                _sample(gdp, "2024-Q2", None, confidentiality=None, state="missing", flag=None),
            ))
            _store(db, dfr.id, "2024-01-01", "2024-01-31", "2024-02-01T10:30:00+00:00", (
                _sample(dfr, "2024-01-03", "2.00"),
            ))
            _store(db, dfr.id, "2024-02-01", "2024-02-09", "2024-02-05T10:30:00+00:00", (
                _sample(dfr, "2024-02-01", "2.10"),
            ))
            _store(db, dfr.id, "2024-02-10", "2024-02-12", "2024-02-13T10:30:00+00:00", (
                _sample(dfr, "2024-02-10", "2.25"),
            ))
            # Explicit provider-vintage captures cannot bridge the snapshot gap.
            _store(db, dfr.id, "2024-02-06", "2024-02-09", "2024-02-14T10:30:00+00:00", history=True)
        yield dsn


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, macro._require_local, lambda: None)
    with TestClient(app) as test_client:
        yield test_client


def test_exact_catalog_and_validation_without_database(client):
    catalog = client.get("/macro/indicators").json()
    assert [item["id"] for item in catalog] == list(CATALOG)
    assert all(item["reuse_url"].startswith("https://") and item["attribution"] for item in catalog)
    assert catalog[1]["multiplier"] == 6 and catalog[3]["frequency"] == "quarterly"
    valid = "?start=2024-01-02&end=2024-01-03"
    assert client.get("/macro/indicators/not-an-indicator/history" + valid).status_code == 404
    route = "/macro/indicators/eur-usd/history"
    for query in (
        "?start=2024-01-02", "?start=2024-1-02&end=2024-01-03", "?start=2024-02-31&end=2024-03-03",
        "?start=2024-01-04&end=2024-01-03", "?start=2000-01-04&end=2024-01-03",
        valid + "&start=2024-01-01", valid + "&unexpected=fixture-input",
    ):
        response = client.get(route + query)
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid macro request"}


def test_loopback_guard_and_public_demo_remain_separate(monkeypatch):
    with TestClient(app) as client:
        assert client.get("/macro/indicators").status_code == 404
        assert client.get("/health").status_code == 200
    monkeypatch.setattr(public_demo, "resolve_demo_settings",
                        lambda _: public_demo.DemoSettings("unused fixture", "unused fixture"))
    paths = {route.path for route in public_demo.create_public_app().routes}
    assert not any(path.startswith("/macro/") for path in paths)


@pytest.mark.parametrize("dsn", [
    "host=198.51.100.1 port=5432 dbname=db user=ro",
    "host=localhost port=5432 dbname=db user=ro",
    "host=127.0.0.1 hostaddr=198.51.100.1 port=5432 dbname=db user=ro",
    "host=127.0.0.1 port=5432 dbname=db user=ro options='-c search_path=public'",
    "host=127.0.0.1 port=5432 dbname=db user=ro service=other",
])
def test_loopback_dsn_rejects_remote_or_overridden_route(dsn):
    with pytest.raises(macro.MacroConfigError):
        macro._loopback_dsn(dsn)


def test_default_reader_uses_explicit_loopback_dsn(monkeypatch):
    class Connection:
        read_only = False
        isolation_level = None

    monkeypatch.delenv("MACRO_API_MODE", raising=False)
    for key in macro._ROUTE_OVERRIDES:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MACRO_API_DSN", "host=127.0.0.1 port=5432 dbname=fixture user=fixture")

    def connect(dsn):
        options = conninfo_to_dict(dsn)
        assert options["host"] == options["hostaddr"] == "127.0.0.1"
        return Connection()

    monkeypatch.setattr(macro.psycopg, "connect", connect)
    reader = macro.open_macro_reader()
    assert reader.read_only
    assert reader.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ


def test_database_errors_are_sanitized(client, monkeypatch):
    def fail():
        raise psycopg.OperationalError("synthetic private details")
    monkeypatch.setattr(macro, "open_macro_reader", fail)
    response = client.get("/macro/indicators/eur-usd/history?start=2024-01-02&end=2024-01-03")
    assert response.status_code == 503 and response.json() == {"detail": "Macro data unavailable"}


@pytest.fixture
def remote_settings(monkeypatch):
    settings = {
        "MARKETPULSE_DEV_PROJECT_REF": DEV_PROJECT_REF,
        "USER_RO": f"marketpulse_api_ro.{DEV_PROJECT_REF}",
        "PASS_USER_RO": "fixture \\ ' @ : ? #",
        "HOST_RO": "aws-0-test.pooler.supabase.com",
        "PORT_RO": "5432",
        "DB_RO": "postgres",
        "SSL_MODE_RO": "verify-full",
        "SSL_ROOT_CERT_RO": conninfo_to_dict(secure_supabase_dsn(""))["sslrootcert"],
    }
    for key in set(settings) | {key for key in macro.os.environ if key.upper().startswith("PG")}:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MARKETPULSE_DISABLE_LOCAL_DOTENV", raising=False)
    monkeypatch.setenv("MACRO_API_MODE", "supabase-dev")
    monkeypatch.setattr(macro, "_read_dotenv", lambda name: settings)
    return settings


def test_remote_reader_preserves_tls_password_and_transaction(monkeypatch, remote_settings):
    class Connection:
        read_only = False
        isolation_level = None

    def connect(dsn):
        options = conninfo_to_dict(dsn)
        assert options["password"] == remote_settings["PASS_USER_RO"]
        assert options["host"] == remote_settings["HOST_RO"]
        assert options["sslmode"] == "verify-full"
        assert options["gssencmode"] == "disable"
        return Connection()

    monkeypatch.setattr(macro, "connect_dev", connect)
    reader = macro.open_macro_reader()
    assert reader.read_only
    assert reader.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ


@pytest.mark.parametrize(("key", "value"), [
    ("HOST_RO", "fixture.invalid"),
    ("PORT_RO", "6543"),
    ("SSL_MODE_RO", "require"),
    ("SSL_MODE_RO", "disable"),
    ("SSL_ROOT_CERT_RO", "missing-fixture.crt"),
    ("PASS_USER_RO", ""),
    ("USER_RO", "postgres"),
    ("MARKETPULSE_DEV_PROJECT_REF", "wrong-project"),
])
def test_invalid_remote_config_fails_before_connection(monkeypatch, remote_settings, key, value):
    remote_settings[key] = value
    monkeypatch.setattr(macro, "connect_dev", lambda _: pytest.fail("unexpected connection"))
    with pytest.raises(macro.MacroConfigError):
        macro.open_macro_reader()


def test_remote_rejects_libpq_override(monkeypatch, remote_settings):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    with pytest.raises(macro.MacroConfigError, match="unexpected PostgreSQL environment"):
        macro.open_macro_reader()


def test_remote_rejects_invalid_certificate(monkeypatch, remote_settings, tmp_path):
    invalid = tmp_path / "invalid.crt"
    invalid.write_text("invalid certificate", encoding="utf-8")
    from backend.market_data import connection
    monkeypatch.setattr(connection, "_CA_CERTIFICATE", invalid)
    remote_settings["SSL_ROOT_CERT_RO"] = str(invalid)
    with pytest.raises(macro.MacroConfigError, match="root certificate invalid"):
        macro.open_macro_reader()


@pytest.mark.parametrize("error_type", [psycopg.errors.InvalidPassword, psycopg.OperationalError])
def test_remote_connection_failure_is_sanitized(client, monkeypatch, remote_settings, caplog, error_type):
    def fail(_):
        raise error_type("synthetic private details")

    monkeypatch.setattr(macro, "connect_dev", fail)
    response = client.get("/macro/indicators/eur-usd/history?start=2024-01-02&end=2024-01-03")
    assert response.status_code == 503
    assert response.json() == {"detail": "Macro data unavailable"}
    assert "Supabase DEV verified connection failed" in caplog.text
    assert "synthetic private details" not in caplog.text


def test_decimal_strings_period_bounds_confidentiality_and_read_only(client, api_dsn, monkeypatch):
    monkeypatch.setenv("MACRO_API_DSN", api_dsn)
    response = client.get("/macro/indicators/eur-usd/history?start=2024-01-02&end=2024-01-04")
    assert response.status_code == 200
    history = response.json()
    assert history["updated_at"] == "2024-01-05T10:30:00Z"
    assert history["anchor"] is history["coverage_start"] is history["coverage_end"] is None
    assert [(row["period_raw"], row["value"], row["status_kind"]) for row in history["observations"]] == [
        ("2024-01-02", "1.2300", "normal"), ("2024-01-04", None, "withheld"),
    ]
    quarterly = client.get("/macro/indicators/real-gdp-qoq/history?start=2024-02-29&end=2024-04-01").json()
    assert [(row["period_raw"], row["period_end"], row["value"]) for row in quarterly["observations"]] == [
        ("2024-Q1", "2024-03-31", "-0.4"), ("2024-Q2", "2024-06-30", None),
    ]
    with macro.open_macro_reader() as reader:
        assert reader.execute("SHOW transaction_read_only").fetchone() == ("on",)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            reader.execute("DELETE FROM macro_data.series WHERE false")
        reader.rollback()


def test_deposit_anchor_and_coverage_do_not_cross_gap(client, api_dsn, monkeypatch):
    monkeypatch.setenv("MACRO_API_DSN", api_dsn)
    url = "/macro/indicators/deposit-rate/history"
    history = client.get(url + "?start=2024-02-01&end=2024-02-20").json()
    assert history["anchor"]["effective_date"] == "2024-01-03"
    assert (history["coverage_start"], history["coverage_end"]) == ("2024-02-01", "2024-02-05")
    assert [row["effective_date"] for row in history["observations"]] == ["2024-02-01", "2024-02-10"]
    assert history["updated_at"] == "2024-02-13T10:30:00Z"
    after_gap = client.get(url + "?start=2024-02-09&end=2024-02-12").json()
    assert after_gap["anchor"] is None
    assert (after_gap["coverage_start"], after_gap["coverage_end"]) == ("2024-02-10", "2024-02-12")
    assert after_gap["observations"][0]["effective_date"] == "2024-02-10"
    uncovered = client.get(url + "?start=2024-02-06&end=2024-02-09").json()
    assert uncovered["anchor"] is uncovered["coverage_start"] is uncovered["coverage_end"] is None
    assert uncovered["observations"] == []
