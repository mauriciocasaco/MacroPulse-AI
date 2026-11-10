from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import os
import subprocess
import sys
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend import public_demo as demo
from backend.market_data.dev_import import DEV_PROJECT_REF


SECRET = sha256(b"MarketPulse synthetic test fixture").hexdigest()
REF = "abcdefghijklmnopqrst"
PASSWORD = "fixture-0123456789-ABCDEFG"
DSN = f"host=db.{REF}.supabase.co port=5432 dbname=postgres user=marketpulse_demo_ro password={PASSWORD}"
SETTINGS = {
    "MARKETPULSE_DEMO_PROJECT_REF": REF,
    "MARKETPULSE_DEMO_DSN": DSN,
    "MARKETPULSE_DEMO_ORIGIN_SECRET": SECRET,
}
HEADERS = {demo.ORIGIN_HEADER: SECRET, demo.CLIENT_IP_HEADER: "192.0.2.3"}
WINDOW = {"start": "2020-01-01", "end_exclusive": "2020-01-03"}
ANALYSIS = {"question": "What happened?", "event": "covid", "asset": "synthetic_1", **WINDOW}


@pytest.fixture
def demo_env(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith(("PG", "MARKETPULSE_", "MARKET_ANALYSIS_")) or key in {
            "DEBUG", "MARKET_API_DSN", "GEMINI_API_KEY", "GOOGLE_API_KEY"
        }:
            monkeypatch.delenv(key, raising=False)
    for key, value in SETTINGS.items():
        monkeypatch.setenv(key, value)
    cert = tmp_path / "fixture-ca.pem"
    cert.write_text("Fixture path; this test never opens a TLS socket")
    monkeypatch.setenv("MARKETPULSE_DEMO_SSL_ROOT_CERT", str(cert))


@pytest.fixture
def client(demo_env, monkeypatch):
    first = date(2020, 1, 1)
    last = date(2020, 1, 2)
    connection = MagicMock()
    connection.__enter__.return_value = connection

    def select(query, params=()):
        cursor = MagicMock()
        if "GROUP BY" in query:
            cursor.fetchall.return_value = [{"series_id": 1, "first": first, "last": last, "count": 2}]
        elif "min(bar_date)" in query:
            cursor.fetchone.return_value = {"first": first, "last": last, "count": 2}
        else:
            assert params == (1, first, date(2020, 1, 3), 2600)
            cursor.fetchall.return_value = [{
                "bar_date": first, "close": Decimal("123.00"),
                "generated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            }]
        return cursor

    connection.execute.side_effect = select
    monkeypatch.setattr(demo, "connect_demo", lambda dsn: connection)
    with TestClient(demo.create_public_app()) as http:
        yield http


@pytest.mark.parametrize("key,value", [
    ("MARKETPULSE_DEMO_ORIGIN_SECRET", ""),
    ("MARKETPULSE_DEMO_ORIGIN_SECRET", "example"),
    ("MARKETPULSE_DEMO_ORIGIN_SECRET", "0" * 64),
    ("MARKETPULSE_DEMO_ORIGIN_SECRET", "0123456789abcdef" * 4),
    ("MARKETPULSE_DEMO_PROJECT_REF", ""),
    ("MARKETPULSE_DEMO_PROJECT_REF", DEV_PROJECT_REF),
    ("MARKETPULSE_DEMO_DSN", ""),
    ("MARKETPULSE_DEMO_DSN", DSN.replace(REF, DEV_PROJECT_REF)),
    ("MARKETPULSE_DEMO_DSN", DSN.replace("marketpulse_demo_ro", "postgres")),
    ("MARKETPULSE_DEMO_DSN", DSN.replace(PASSWORD, "changeme")),
    ("MARKETPULSE_DEMO_DSN", DSN + " sslmode=disable"),
    ("MARKETPULSE_DEMO_DSN", DSN + " hostaddr=192.0.2.1"),
    ("MARKETPULSE_DEMO_DSN", DSN.replace("5432", "6543")),
    ("MARKETPULSE_DEMO_SSL_ROOT_CERT", "missing"),
    ("DEBUG", "true"),
    ("MARKETPULSE_DEBUG", "1"),
    ("MARKETPULSE_DEMO_ANALYSIS_ENABLED", "true"),
    ("MARKETPULSE_DEMO_CATALOG_PER_MINUTE", "31"),
    ("MARKETPULSE_DEMO_CONCURRENCY", "0"),
    ("MARKET_API_DSN", ""),
    ("GEMINI_API_KEY", "fixture"),
    ("PGSERVICE", "fixture"),
])
def test_unsafe_configuration_fails_closed(demo_env, key, value):
    with pytest.raises(demo.DemoConfigurationError) as error:
        demo.resolve_demo_settings({**os.environ, key: value})
    assert SECRET not in str(error.value) and PASSWORD not in str(error.value)


def test_supported_tls_target_and_private_settings(demo_env):
    dsn = DSN.replace(f"db.{REF}.supabase.co", "aws-0-fixture.pooler.supabase.com").replace(
        "user=marketpulse_demo_ro", f"user=marketpulse_demo_ro.{REF}"
    )
    ca = os.environ["MARKETPULSE_DEMO_SSL_ROOT_CERT"]
    settings = demo.resolve_demo_settings({**SETTINGS, "MARKETPULSE_DEMO_DSN": dsn, "MARKETPULSE_DEMO_SSL_ROOT_CERT": ca})
    assert conninfo_to_dict(settings.dsn) == {
        **conninfo_to_dict(dsn), "sslmode": "verify-full", "sslrootcert": ca,
        "gssencmode": "disable", "connect_timeout": "5",
    }
    assert SECRET not in repr(settings) and PASSWORD not in repr(settings)


@pytest.mark.parametrize("mode,expected", [("local", "200"), ("test", "503"), ("public_demo", "")])
def test_entrypoint_mode_isolation(demo_env, monkeypatch, mode, expected):
    monkeypatch.setenv("MARKETPULSE_MODE", mode)
    monkeypatch.delenv("MARKETPULSE_DEMO_ORIGIN_SECRET")
    result = subprocess.run(
        [sys.executable, "-c", (
            "from fastapi.testclient import TestClient; from backend.api import app; "
            f"print(TestClient(app).get({'/health' if mode == 'local' else '/assets'!r}).status_code)"
        )],
        capture_output=True, text=True, timeout=15,
    )
    assert result.stdout.strip() == expected
    if mode == "public_demo":
        assert result.returncode != 0
        assert "Public demo origin secret is missing or invalid" in result.stderr
    else:
        assert result.returncode == 0


def test_only_authenticated_allowlisted_routes(client):
    assert client.get("/assets").status_code == 403
    assert client.get("/assets", headers={**HEADERS, demo.ORIGIN_HEADER: "incorrect"}).status_code == 403
    assert client.get("/assets", headers={demo.ORIGIN_HEADER: SECRET, "X-Forwarded-For": "192.0.2.3"}).status_code == 403
    assert client.get("/assets", headers=HEADERS).status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json", "/health", "/admin", "/compare",
                 "/assets/1/latest", "/assets/4/history", "/assets/"):
        assert client.get(path, headers=HEADERS).status_code == 404
    assert client.post("/assets", headers=HEADERS).status_code == 405


def test_history_catalog_and_validation(client):
    catalog = client.get("/assets", headers=HEADERS)
    assert catalog.headers["cache-control"] == "no-store"
    asset, = catalog.json()
    assert asset["provider_name"] == "MarketPulse AI synthetic dataset"
    assert asset["economic_unit"] == "Synthetic index points"
    history = client.get("/assets/1/history", params=WINDOW, headers=HEADERS)
    assert history.status_code == 200
    result = history.json()
    assert result["asset"] == asset
    assert result["bars"][0]["source_convention"] == demo.CONVENTION
    assert result["bars"][0]["retrieved_at"] == "2025-01-01T00:00:00Z"
    for params in ({}, {**WINDOW, "start": "2010-01-01"}, {**WINDOW, "extra": "x"}):
        invalid = client.get("/assets/1/history", params=params, headers=HEADERS)
        assert invalid.status_code == 422
        assert invalid.json() == {"detail": "Invalid request parameters"}
    duplicate = client.get("/assets/1/history", params=[*WINDOW.items(), ("start", "2020-01-02")], headers=HEADERS)
    assert duplicate.status_code == 400


def test_disabled_analysis_never_calls_database_or_model(client, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Disabled analysis started work")

    monkeypatch.setattr(demo, "connect_demo", unexpected)
    monkeypatch.setattr("backend.api.analyze", unexpected)
    monkeypatch.setattr("backend.analysis_provider.GeminiProvider.generate", unexpected)
    assert client.post("/analysis", headers=HEADERS, json={**ANALYSIS, "question": "x" * 501}).status_code == 422
    assert client.post("/analysis", headers=HEADERS, content=b"no").status_code == 415
    assert client.post("/analysis", headers=HEADERS, content=b"x" * 8193).status_code == 413
    assert client.post("/analysis", headers={**HEADERS, "Content-Type": "application/json"},
                       content=iter((b"x" * 4097, b"x" * 4096))).status_code == 413
    response = client.post("/analysis", headers=HEADERS, json=ANALYSIS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Public demo analysis is disabled"}


def test_quota_is_per_operation_and_ip(client, monkeypatch):
    monkeypatch.setattr(demo, "monotonic", lambda: 61)
    for _ in range(30):
        assert client.get("/assets", headers=HEADERS).status_code == 200
    blocked = client.get("/assets", headers={**HEADERS, "X-Forwarded-For": "192.0.2.5"})
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "59"
    assert client.get("/assets", headers={**HEADERS, demo.CLIENT_IP_HEADER: "192.0.2.4"}).status_code == 200
    for _ in range(20):
        assert client.get("/assets/1/history", params=WINDOW, headers=HEADERS).status_code == 200
    assert client.get("/assets/1/history", params=WINDOW, headers=HEADERS).status_code == 429
    monkeypatch.setattr(demo, "monotonic", lambda: 120)
    assert client.get("/assets", headers=HEADERS).status_code == 200


def test_concurrency_limit(demo_env, monkeypatch):
    monkeypatch.setenv("MARKETPULSE_DEMO_CONCURRENCY", "1")
    entered, release = Event(), Event()

    def wait(_repo):
        entered.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(demo.DemoRepository, "catalog", wait)
    monkeypatch.setattr(demo, "connect_demo", lambda _dsn: MagicMock())
    with TestClient(demo.create_public_app()) as client, ThreadPoolExecutor(1) as pool:
        request = pool.submit(client.get, "/assets", headers=HEADERS)
        try:
            assert entered.wait(5)
            busy = client.get("/assets", headers=HEADERS)
            assert busy.status_code == 503 and busy.headers["retry-after"] == "1"
        finally:
            release.set()
        assert request.result(timeout=5).status_code == 200


def test_repository_errors_never_echo_internals(client, monkeypatch):
    def unavailable(_dsn):
        raise psycopg.OperationalError(DSN)

    monkeypatch.setattr(demo, "connect_demo", unavailable)
    response = client.get("/assets", headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Public demo service unavailable"}
    assert PASSWORD not in response.text


def test_startup_refuses_unverified_database(demo_env, monkeypatch):
    def unavailable(_dsn):
        raise psycopg.OperationalError(DSN)

    monkeypatch.setattr(demo, "connect_demo", unavailable)
    with pytest.raises(demo.DemoConfigurationError, match="database startup verification failed") as error:
        with TestClient(demo.create_public_app()):
            pytest.fail("Unverified database did not block startup")
    assert PASSWORD not in str(error.value) and error.value.__suppress_context__


@pytest.mark.parametrize("tls,role,scope", [(True, True, True), (False, True, True),
                                          (True, False, True), (True, True, False)])
def test_connection_requires_tls_and_read_only_privileges(monkeypatch, tls, role, scope):
    connection = MagicMock()
    connection.pgconn.ssl_in_use = tls
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.return_value.fetchone.side_effect = [(role,), (scope,)]
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(demo.psycopg, "connect", connect)
    if tls and role and scope:
        assert demo.connect_demo(DSN) is connection
        assert connection.read_only
        assert "statement_timeout" in connection.execute.call_args_list[0].args[0]
        assert "lock_timeout" in connection.execute.call_args_list[1].args[0]
        assert not connection.close.called
    else:
        with pytest.raises(demo.DemoConfigurationError):
            demo.connect_demo(DSN)
        connection.close.assert_called_once()
