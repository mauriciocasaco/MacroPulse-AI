from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from types import SimpleNamespace
import subprocess
import sys
from threading import Event
from time import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend import public_demo as demo
from backend.macro_ai import v2_retrieval, v2_service
from backend.macro_ai.public_gemini import PREFLIGHT_TOKENS
from backend.macro_data.api import MacroHistory, MacroObservation, indicator_metadata
from backend.macro_data.contracts import parse_period
from backend.market_data.dev_import import DEV_PROJECT_REF


SECRET = sha256(b"MarketPulse synthetic test fixture").hexdigest()
REF = "abcdefghijklmnopqrst"
SHARED_FLAG = "MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT"
PASSWORD = "fixture-0123456789-ABCDEFG"
DSN = f"host=db.{REF}.supabase.co port=5432 dbname=postgres user=marketpulse_demo_ro password={PASSWORD}"
SHARED_DSN = DSN.replace(REF, DEV_PROJECT_REF)
SETTINGS = {
    "MARKETPULSE_DEMO_PROJECT_REF": REF,
    "MARKETPULSE_DEMO_DSN": DSN,
    "MARKETPULSE_DEMO_ORIGIN_SECRET": SECRET,
}
HEADERS = {demo.ORIGIN_HEADER: SECRET, demo.CLIENT_IP_HEADER: "192.0.2.3"}
CATALOG = "/macro/indicators"
HISTORY = CATALOG + "/eur-usd/history"
RESEARCH = "/macro/analysis"
ANALYSIS = {"question": "What happened?", "event_id": "covid", "indicator_id": "eur-usd",
            "start": "2020-02-01", "end": "2020-12-31", "research_version": "v2"}


def research_headers():
    return {**HEADERS, "X-MacroPulse-Research-Expires": str(int(time() * 1000) + 60_000)}


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
    monkeypatch.setattr(demo, "connect_demo", lambda dsn: MagicMock())
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
    assert not settings.shared_project


@pytest.mark.parametrize("pooler", [False, True])
def test_shared_settings_pin_project_role_and_tls(demo_env, pooler):
    dsn = SHARED_DSN
    if pooler:
        dsn = dsn.replace(f"db.{DEV_PROJECT_REF}.supabase.co", "aws-0-fixture.pooler.supabase.com").replace(
            "user=marketpulse_demo_ro", f"user=marketpulse_demo_ro.{DEV_PROJECT_REF}"
        )
    ca = os.environ["MARKETPULSE_DEMO_SSL_ROOT_CERT"]
    settings = demo.resolve_demo_settings({
        **os.environ, SHARED_FLAG: "true", "MARKETPULSE_DEMO_PROJECT_REF": DEV_PROJECT_REF, "MARKETPULSE_DEMO_DSN": dsn,
    })
    assert settings.shared_project
    assert conninfo_to_dict(settings.dsn) == {
        **conninfo_to_dict(dsn), "sslmode": "verify-full", "sslrootcert": ca,
        "gssencmode": "disable", "connect_timeout": "5",
    }


@pytest.mark.parametrize("overrides", [
    {},
    {SHARED_FLAG: "false"},
    {SHARED_FLAG: "True"},
    {SHARED_FLAG: "1"},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_PROJECT_REF": REF, "MARKETPULSE_DEMO_DSN": DSN},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": DSN},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": SHARED_DSN.replace("marketpulse_demo_ro", "marketpulse_api_ro")},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": SHARED_DSN.replace("marketpulse_demo_ro", "postgres")},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": SHARED_DSN.replace(
        f"db.{DEV_PROJECT_REF}.supabase.co", "aws-0-fixture.pooler.supabase.com"
    ).replace("marketpulse_demo_ro", f"marketpulse_demo_ro.{REF}")},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": SHARED_DSN + " sslmode=require"},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": "", "MARKET_API_DSN": SHARED_DSN},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": "", "MACRO_API_DSN": SHARED_DSN},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": "", "DATABASE_URL": SHARED_DSN},
    {SHARED_FLAG: "true", "MARKETPULSE_DEMO_DSN": "", "PASS_USER_RO": PASSWORD},
])
def test_shared_settings_reject_unapproved_or_private_configuration(demo_env, overrides):
    with pytest.raises(demo.DemoConfigurationError) as error:
        demo.resolve_demo_settings({
            **os.environ, "MARKETPULSE_DEMO_PROJECT_REF": DEV_PROJECT_REF, "MARKETPULSE_DEMO_DSN": SHARED_DSN,
            **overrides,
        })
    assert PASSWORD not in str(error.value)


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
    assert client.get(CATALOG).status_code == 403
    assert client.get(CATALOG, headers={**HEADERS, demo.ORIGIN_HEADER: "incorrect"}).status_code == 403
    assert client.get(CATALOG, headers={demo.ORIGIN_HEADER: SECRET, "X-Forwarded-For": "192.0.2.3"}).status_code == 403
    assert client.get(CATALOG, headers=HEADERS).status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json", "/health", "/admin", "/compare",
                 "/assets", "/assets/1/history", "/analysis"):
        assert client.get(path, headers=HEADERS).status_code == 404
    assert client.post(CATALOG, headers=HEADERS).status_code == 405
    assert client.post(RESEARCH, json=ANALYSIS).status_code == 403
    assert client.post(RESEARCH, headers={**research_headers(), demo.ORIGIN_HEADER: "bad"}, json=ANALYSIS).status_code == 403


def test_disabled_research_preserves_catalog(client, monkeypatch):
    monkeypatch.setattr(demo, "run_public_research", lambda *args: pytest.fail("Disabled research started work"))
    assert client.post(RESEARCH, headers=research_headers(), json=ANALYSIS).status_code == 503
    assert client.get(CATALOG, headers=HEADERS).status_code == 200
    assert demo.RESEARCH_HEADER not in client.get(CATALOG, headers=HEADERS).headers


@pytest.mark.parametrize(("key", "accepted"), [
    ("AIza" + "a" * 35, True),
    ("AQ." + "A1_-b" * 7, True),
    ("AQ." + "a" * 31, False),
    ("AQ." + "a" * 161, False),
    ("AQ." + "a" * 32 + "!", False),
    ("AQ." + "a" * 32 + "\n", False),
    ("AQ." + "a" * 32 + " ", False),
    ("AIza" + "a" * 34, False),
    ("not-a-key", False),
])
def test_public_research_accepts_auth_and_standard_gemini_keys(key, accepted):
    model = "gemini-3.6-flash"
    config = demo._research_config({
        "MARKETPULSE_DEMO_RESEARCH_ENABLED": "true",
        "MARKETPULSE_DEMO_GEMINI_API_KEY": key,
        "MARKETPULSE_DEMO_GEMINI_MODEL": model,
    })
    assert (config is not None) is accepted
    if accepted:
        assert config == (key, model)


def test_public_research_aq_key_enables_catalog_header(demo_env, monkeypatch):
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AQ." + "A1_-b" * 7)
    monkeypatch.setattr(demo, "connect_demo", lambda dsn: MagicMock())
    with TestClient(demo.create_public_app()) as client:
        assert client.get(CATALOG, headers=HEADERS).headers[demo.RESEARCH_HEADER] == "true"


@pytest.fixture
def research_client(demo_env, monkeypatch):
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AIza" + "f" * 35)
    monkeypatch.setattr(demo, "connect_demo", lambda dsn: MagicMock())
    with TestClient(demo.create_public_app()) as client:
        yield client


def test_research_rejects_invalid_body_and_leases(research_client, monkeypatch):
    monkeypatch.setattr(demo, "run_public_research", lambda *args: pytest.fail("Invalid research started work"))
    assert research_client.get(CATALOG, headers=HEADERS).headers[demo.RESEARCH_HEADER] == "true"
    assert research_client.post(RESEARCH, headers=HEADERS, json=ANALYSIS).status_code == 403
    assert research_client.post(RESEARCH, headers={**HEADERS, "X-MacroPulse-Research-Expires": "1234567890123"},
                                json=ANALYSIS).status_code == 403
    for invalid in ({**ANALYSIS, "question": "x" * 501}, {**ANALYSIS, "research_version": "v1"},
                    {**ANALYSIS, "event_id": None}, {**ANALYSIS, "start": 1580515200},
                    {**ANALYSIS, "end": "2021-01-01"}, {**ANALYSIS, "extra": True},
                    {**ANALYSIS, "question": f"Explain {SECRET}"}):
        assert research_client.post(RESEARCH, headers=research_headers(), json=invalid).status_code == 422
    assert research_client.post(RESEARCH, headers=research_headers(), content=b"no").status_code == 415
    assert research_client.post(RESEARCH, headers=research_headers(), content=b"x" * 4097).status_code == 413
    assert research_client.post(RESEARCH, headers={**research_headers(), "Content-Type": "application/json"},
                                content=iter((b"x" * 3000, b"x" * 1097))).status_code == 413
    assert research_client.post(RESEARCH, headers={**research_headers(), "Content-Type": "application/json"},
                                content=b'{"question":"duplicate","question":"again"}').status_code == 422


def test_read_quota_is_per_operation_and_ip(client, monkeypatch):
    monkeypatch.setattr(demo, "monotonic", lambda: 61)
    for _ in range(30):
        assert client.get(CATALOG, headers=HEADERS).status_code == 200
    blocked = client.get(CATALOG, headers={**HEADERS, "X-Forwarded-For": "192.0.2.5"})
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "59"
    assert client.get(CATALOG, headers={**HEADERS, demo.CLIENT_IP_HEADER: "192.0.2.4"}).status_code == 200
    for _ in range(20):
        assert client.get(HISTORY, headers=HEADERS).status_code == 422
    assert client.get(HISTORY, headers=HEADERS).status_code == 429
    monkeypatch.setattr(demo, "monotonic", lambda: 120)
    assert client.get(CATALOG, headers=HEADERS).status_code == 200


def test_research_concurrency_and_timeouts_keep_reads_available(research_client, monkeypatch):
    started = [Event(), Event()]
    release = Event()
    pending = iter(started)

    def wait(*_args):
        next(pending).set()
        assert release.wait(5)
        raise RuntimeError("synthetic unavailable provider")

    monkeypatch.setattr(demo, "run_public_research", wait)
    monkeypatch.setattr(demo, "RESEARCH_SECONDS", 0.15)
    with ThreadPoolExecutor(2) as pool:
        calls = [pool.submit(research_client.post, RESEARCH, headers=research_headers(), json=ANALYSIS)
                 for _ in started]
        try:
            assert all(event.wait(2) for event in started)
            assert research_client.get(CATALOG, headers=HEADERS).status_code == 200
            assert all(call.result(timeout=2).status_code == 504 for call in calls)
            blocked = research_client.post(RESEARCH, headers=research_headers(), json=ANALYSIS)
            assert blocked.status_code == 503 and blocked.headers["Retry-After"] == "1"
        finally:
            release.set()


def test_repository_errors_never_echo_internals(client, monkeypatch):
    def unavailable(_dsn):
        raise psycopg.OperationalError(DSN)

    monkeypatch.setattr(demo, "connect_demo", unavailable)
    response = client.get(CATALOG, headers=HEADERS)
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


def test_shared_startup_connection_failure_never_retries_or_falls_back(demo_env, monkeypatch):
    monkeypatch.setenv(SHARED_FLAG, "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_PROJECT_REF", DEV_PROJECT_REF)
    monkeypatch.setenv("MARKETPULSE_DEMO_DSN", SHARED_DSN)
    connect = MagicMock(side_effect=psycopg.OperationalError("synthetic connection failure"))
    monkeypatch.setattr(demo.psycopg, "connect", connect)
    with pytest.raises(demo.DemoConfigurationError, match="database startup verification failed"):
        with TestClient(demo.create_public_app()):
            pytest.fail("Failed shared connection did not block startup")
    connect.assert_called_once()
    assert conninfo_to_dict(connect.call_args.args[0])["host"] == f"db.{DEV_PROJECT_REF}.supabase.co"


@pytest.mark.parametrize("tls,role,scope", [(True, True, True), (False, True, True),
                                          (True, False, True), (True, True, False)])
def test_connection_requires_tls_and_read_only_privileges(monkeypatch, tls, role, scope):
    connection = MagicMock()
    connection.pgconn.ssl_in_use = tls
    connection.pgconn.info = [SimpleNamespace(keyword=b"sslmode", val=b"verify-full")]
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.return_value.fetchone.side_effect = [(role,), (scope,)]
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(demo.psycopg, "connect", connect)
    settings = demo.DemoSettings(DSN, SECRET)
    verify = MagicMock()
    monkeypatch.setattr(demo, "verify_projection", verify)
    if tls and role and scope:
        assert demo.connect_demo(settings) is connection
        verify.assert_called_once_with(connection, shared_project=False)
        assert connection.read_only
        assert "statement_timeout" in connection.execute.call_args_list[0].args[0]
        assert "lock_timeout" in connection.execute.call_args_list[1].args[0]
        assert not connection.close.called
    else:
        with pytest.raises(demo.DemoConfigurationError):
            demo.connect_demo(settings)
        connection.close.assert_called_once()
        verify.assert_not_called()


@pytest.mark.parametrize("shared", [False, True])
def test_public_research_uses_only_verified_demo_and_cites_evidence(demo_env, monkeypatch, shared):
    question = "How did the war in Ukraine relate to euro-area inflation?"
    connections = []
    connection = object()
    ref = DEV_PROJECT_REF if shared else REF
    if shared:
        monkeypatch.setenv(SHARED_FLAG, "true")
        monkeypatch.setenv("MARKETPULSE_DEMO_PROJECT_REF", ref)
        monkeypatch.setenv("MARKETPULSE_DEMO_DSN", SHARED_DSN)
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AIza" + "f" * 35)

    @contextmanager
    def connect(settings):
        assert settings.shared_project is shared
        assert conninfo_to_dict(settings.dsn)["host"] == f"db.{ref}.supabase.co"
        connections.append(settings)
        yield connection

    def read(db, item, window, *, max_points):
        assert db is connection and item.id == "hicp-yoy" and max_points == 2600
        observations = []
        for raw, value in [("2022-02", "5.9"), ("2022-09", "9.9"), ("2022-10", "10.6")]:
            period = parse_period(item.frequency, raw)
            observations.append(MacroObservation(
                period_raw=raw, period_start=period.start, period_end=period.end, effective_date=None,
                value=Decimal(value), status_kind="normal", status_raw=None,
            ))
        return MacroHistory(indicator=indicator_metadata(item), start=window.start, end=window.end,
                            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc), observations=observations)

    monkeypatch.setattr(demo, "connect_demo", connect)
    monkeypatch.setattr(demo, "read_history", read)
    monkeypatch.setattr(v2_service, "read_statistics", lambda *_args: pytest.fail("Local reader used"))
    monkeypatch.setattr(v2_retrieval, "semantic", lambda *_args: pytest.fail("E5 loaded"))
    requests = []
    intent = {
        "event": "ukraine", "purpose": "causal", "requirements": [{
            "excerpt": question, "indicator": "hicp-yoy", "geography": "euro_area",
            "measure": "annual_inflation", "operation": "trend",
        }],
    }

    def simulate(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "AIza" + "f" * 35
        payload = json.loads(request.content)
        assert SECRET not in request.content.decode() and PASSWORD not in request.content.decode()
        requests.append(payload)
        if request.url.path.endswith(":countTokens"):
            assert payload["generateContentRequest"]["model"] == "models/gemini-3.6-flash"
            return httpx.Response(200, json={"totalTokens": 200})
        assert payload["generationConfig"]["maxOutputTokens"] == 2400
        assert payload["generationConfig"]["thinkingConfig"]["includeThoughts"] is False
        assert "tools" not in payload
        assert {key: value for key, value in requests[-2]["generateContentRequest"].items() if key != "model"} == payload
        if len(requests) == 2:
            answer = intent
        else:
            content = payload["contents"][0]["parts"][0]["text"]
            rows = json.loads(content.split("\n", 1)[1])
            answer = {"sentences": [{"evidence_id": row["evidence_id"], "text": row["sentences"][0]} for row in rows]}
        return httpx.Response(200, json={
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 60, "thoughtsTokenCount": 40},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(answer)}]}}],
        })

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(simulate), **options,
    ))
    params = {**ANALYSIS, "question": question, "indicator_id": "hicp-yoy", "event_id": "ukraine",
              "start": "2022-02-01", "end": "2024-02-29"}
    with TestClient(demo.create_public_app()) as research_client:
        assert len(connections) == 1  # Startup verifies the same dedicated target.
        connections.clear()
        reply = research_client.post(RESEARCH, headers=research_headers(), json=params)
        assert len(connections) == 1 and len(requests) == 4
        monkeypatch.setattr(demo, "read_history", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            psycopg.OperationalError("Synthetic DEMO failure"),
        ))
        requests.clear()
        failure = research_client.post(RESEARCH, headers=research_headers(), json=params)
    assert reply.status_code == 200, reply.text
    assert reply.headers["X-MacroPulse-Research-Complete"] == "true"
    result = reply.json()
    assert result["research_v2"] is True and result["support"] == "partial"
    assert result["narrative_kind"] == "gemini"
    assert {row["kind"] for row in result["evidence"]} == {"series", "document"}
    assert {ref for claim in result["narrative"] for ref in claim["citations"]} <= {
        row["id"] for row in result["evidence"]
    }
    assert any("does not identify" in limitation for limitation in result["limitations"])
    assert failure.status_code == 503
    assert len(connections) == 2 and len(requests) == 2


@pytest.mark.parametrize("failure,code", [("input limit", 503), ("provider failure", 503), ("timeout", 504)])
def test_public_gemini_fails_closed_without_retries(research_client, monkeypatch, failure, code):
    paths = []

    def simulate(request):
        paths.append(request.url.path)
        if failure == "input limit":
            return httpx.Response(200, json={"totalTokens": PREFLIGHT_TOKENS + 1})
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic provider deadline")
        return httpx.Response(503, text="synthetic private provider failure")

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(simulate), **options,
    ))
    reply = research_client.post(RESEARCH, headers=research_headers(), json=ANALYSIS)
    assert reply.status_code == code and "synthetic" not in reply.text
    assert len(paths) == 1 and paths[0].endswith(":countTokens")
