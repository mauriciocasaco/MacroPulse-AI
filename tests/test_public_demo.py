from asyncio import Event as AsyncEvent, timeout as asyncio_timeout
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import logging
import os
from types import SimpleNamespace
import subprocess
import sys
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend import public_demo as demo
from backend.macro_ai import public_gemini, v2_retrieval, v2_service
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
RESEARCH_ID = "cb31e1d0-41f6-4a82-8436-30f47564c5cb"
ANALYSIS = {"question": "What happened?", "event_id": "covid", "indicator_id": "eur-usd",
            "start": "2020-02-01", "end": "2020-12-31", "research_version": "v2"}


@pytest.fixture
def demo_env(monkeypatch, tmp_path, research_attestation):
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


def test_only_authenticated_allowlisted_routes(client, research_attestation):
    assert client.get(CATALOG).status_code == 403
    assert client.get(CATALOG, headers={**HEADERS, demo.ORIGIN_HEADER: "incorrect"}).status_code == 403
    assert client.get(CATALOG, headers={demo.ORIGIN_HEADER: SECRET, "X-Forwarded-For": "192.0.2.3"}).status_code == 403
    assert client.get(CATALOG, headers=HEADERS).status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json", "/health", "/admin", "/compare",
                 "/assets", "/assets/1/history", "/analysis"):
        assert client.get(path, headers=HEADERS).status_code == 404
    assert client.post(CATALOG, headers=HEADERS).status_code == 405
    assert client.post(RESEARCH, json=ANALYSIS).status_code == 403
    assert client.post(RESEARCH, headers={**research_attestation.headers(ANALYSIS), demo.ORIGIN_HEADER: "bad"}, json=ANALYSIS).status_code == 403


def test_disabled_research_preserves_catalog(client, monkeypatch, research_attestation):
    monkeypatch.setattr(demo, "run_public_research", lambda *args: pytest.fail("Disabled research started work"))
    assert client.post(RESEARCH, headers=research_attestation.headers(ANALYSIS), json=ANALYSIS).status_code == 503
    assert client.get(CATALOG, headers=HEADERS).status_code == 200
    assert demo.RESEARCH_HEADER not in client.get(CATALOG, headers=HEADERS).headers


def test_catalog_head_uses_current_capability_without_database_access(research_client, monkeypatch):
    monkeypatch.setattr(demo, "connect_demo", lambda *_: pytest.fail("HEAD opened a database connection"))
    reply = research_client.head(CATALOG, headers=HEADERS)
    assert reply.status_code == 200 and reply.content == b""
    assert reply.headers[demo.RESEARCH_HEADER] == "true"
    assert reply.headers["X-MacroPulse-Public-Cache"] == "official-v1"
    assert reply.headers["Cache-Control"] == "no-store"
    assert research_client.head(CATALOG).status_code == 403
    assert research_client.head(CATALOG + "?extra=1", headers=HEADERS).status_code == 400
    assert research_client.head(HISTORY, headers=HEADERS).status_code == 405


def test_cache_opt_in_is_only_on_successful_public_reads(client, research_attestation):
    reply = client.get(CATALOG, headers=HEADERS)
    assert reply.headers["X-MacroPulse-Public-Cache"] == "official-v1"
    assert reply.headers["Cache-Control"] == "no-store"
    assert demo.RESEARCH_HEADER not in client.head(CATALOG, headers=HEADERS).headers
    for response in [client.get(CATALOG), client.get(HISTORY, headers=HEADERS),
                     client.post(RESEARCH, headers=research_attestation.headers(ANALYSIS), json=ANALYSIS)]:
        assert response.status_code >= 400
        assert "X-MacroPulse-Public-Cache" not in response.headers


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


def test_research_rejects_invalid_body_and_leases(research_client, monkeypatch, research_attestation):
    now = 1_800_000_000
    monkeypatch.setattr(demo, "time", lambda: now)
    monkeypatch.setattr(demo, "run_public_research", lambda *args: pytest.fail("Invalid research started work"))
    assert research_client.get(CATALOG, headers=HEADERS).headers[demo.RESEARCH_HEADER] == "true"
    assert research_client.post(RESEARCH, headers=HEADERS, json=ANALYSIS).status_code == 403
    expiration = "X-MacroPulse-Research-Expires"
    expired = {**research_attestation.headers(ANALYSIS), expiration: "invalid"}
    assert research_client.post(RESEARCH, headers=expired, json=ANALYSIS).status_code == 403
    for offset in (-1, 0, 60_000, 67_000, 95_001):
        assert research_client.post(RESEARCH, headers=research_attestation.headers(ANALYSIS, expires_in=offset),
                                    json=ANALYSIS).status_code == 403
    headers = research_attestation.headers(ANALYSIS)
    assert research_client.post(RESEARCH, headers=[*headers.items(), (expiration, headers[expiration])],
                                json=ANALYSIS).status_code == 403
    assert research_client.post(RESEARCH, headers={**headers, demo.ORIGIN_HEADER: "invalid"},
                                json=ANALYSIS).status_code == 403
    for offset in (67_001, 90_000, 95_000):
        # Admission reaches the parser, which rejects the synthetic body.
        assert research_client.post(RESEARCH, headers=research_attestation.headers({}, expires_in=offset),
                                    json={}).status_code == 422
    for invalid in ({**ANALYSIS, "question": "x" * 501}, {**ANALYSIS, "research_version": "v1"},
                    {**ANALYSIS, "event_id": None}, {**ANALYSIS, "start": 1580515200},
                    {**ANALYSIS, "end": "2021-01-01"}, {**ANALYSIS, "extra": True},
                    {**ANALYSIS, "question": f"Explain {SECRET}"}):
        assert research_client.post(RESEARCH, headers=research_attestation.headers(invalid), json=invalid).status_code == 422
    assert research_client.post(RESEARCH, headers={name: value for name, value in research_attestation.headers(content=b"no").items()
                                                    if name != "Content-Type"}, content=b"no").status_code == 415
    assert research_client.post(RESEARCH, headers=research_attestation.headers(content=b"x" * 4097), content=b"x" * 4097).status_code == 413
    assert research_client.post(RESEARCH, headers=research_attestation.headers(content=b"x" * 4097),
                                content=iter((b"x" * 3000, b"x" * 1097))).status_code == 413
    duplicate = b'{"question":"duplicate","question":"again"}'
    assert research_client.post(RESEARCH, headers=research_attestation.headers(content=duplicate),
                                content=duplicate).status_code == 422


def test_general_fallback_fits_render_response_and_marks_the_request_complete(research_client, monkeypatch, research_attestation):
    monkeypatch.setenv(demo.GENERAL_FALLBACK_FLAG, "true")
    question = "How would one calculate an off-catalog index during a pandemic?"
    answer = ("Compare equivalent observations using the same definition and geographic scope. " * 32)[:2400].strip()
    paths = []
    clock = [0]
    monkeypatch.setattr(demo, "monotonic", lambda: clock[0])
    monkeypatch.setattr(public_gemini, "monotonic", lambda: clock[0])

    def provider(request):
        paths.append(request.url.path)
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 20)
        clock[0] += 12
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 500})
        output = ({"purpose": "descriptive", "requirements": [{"excerpt": question, "operation": "other"}]}
                  if len(paths) == 2 else {"answer": answer})
        return httpx.Response(200, json={
            "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 600},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(output)}]}}],
        })

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(provider), **options,
    ))
    reply = research_attestation.post(research_client, {**ANALYSIS, "question": question})
    assert reply.status_code == 200 and reply.headers["X-MacroPulse-Research-Complete"] == "true"
    assert len(reply.content) < demo.MAX_RESEARCH_RESPONSE == 65_536
    body = reply.json()
    assert body["status"] == "insufficient_evidence" and body["evidence"] == body["narrative"] == []
    assert body["general_fallback"]["answer"] == answer and body["general_fallback"]["verified"] is False
    assert len(paths) == 4
    assert clock[0] == 48


@pytest.mark.parametrize("slow_call", ["countTokens", "generateContent"])
def test_gemini_stage_has_one_25_second_budget_for_both_http_calls(
    research_client, monkeypatch, caplog, slow_call,
    research_attestation,
):
    clock = [0]
    paths = []
    monkeypatch.setattr(demo, "monotonic", lambda: clock[0])
    monkeypatch.setattr(public_gemini, "monotonic", lambda: clock[0])

    def provider(request):
        operation = request.url.path.split(":")[-1]
        paths.append(operation)
        duration = {"countTokens": 12, "generateContent": 13}
        if slow_call == "countTokens":
            duration["countTokens"] = 25
        clock[0] += duration[operation]
        return httpx.Response(200, json={"totalTokens": 500})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(provider), **options,
    ))
    with caplog.at_level(logging.INFO, logger="uvicorn.error.macropulse.research"):
        reply = research_client.post(RESEARCH, headers=research_attestation.headers(ANALYSIS), json=ANALYSIS)
    assert reply.status_code == 504
    assert "X-MacroPulse-Research-Complete" not in reply.headers
    assert paths == (["countTokens", "countTokens"] if slow_call == "countTokens"
                     else ["countTokens", "generateContent", "countTokens"])
    stage = "gemini.count_tokens" if slow_call == "countTokens" else "gemini.generate_content"
    assert any(record.get("stage") == stage and record.get("error_type") == "TimeoutError"
               for record in map(lambda entry: json.loads(entry.message), caplog.records))


@pytest.mark.parametrize("elapsed,budget", [(0, 25), (50, 13)])
def test_fallback_provider_cancels_a_stalled_call_within_render_budget(monkeypatch, elapsed, budget):
    monkeypatch.setattr(public_gemini, "monotonic", lambda: elapsed)
    paths = []

    def short_timeout(seconds):
        assert seconds == budget
        return asyncio_timeout(0.02)

    async def provider(request):
        paths.append(request.url.path.split(":")[-1])
        await AsyncEvent().wait()
        pytest.fail("The stalled provider was not cancelled")

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(provider), **options,
    ))
    monkeypatch.setattr(public_gemini, "timeout", short_timeout)
    gemini = public_gemini.PublicGemini(api_key="synthetic", model="gemini-3.6-flash", deadline=65)
    with pytest.raises(TimeoutError):
        gemini.general(demo.parse_research(json.dumps(ANALYSIS).encode()), "unsupported_requirement",
                       event=None, knowledge_cutoff=None)
    assert paths == ["countTokens"]


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


def test_research_concurrency_and_timeouts_keep_reads_available(research_client, monkeypatch, research_attestation):
    started = [Event(), Event()]
    release = Event()
    pending = iter(started)

    def wait(_settings, _params, _key, _model, deadline):
        assert 64 < deadline - demo.monotonic() <= 65
        next(pending).set()
        assert release.wait(5)
        return SimpleNamespace(model_dump_json=lambda: pytest.fail("A late result was serialized"))

    # Use a short real asyncio timer after checking the production budget.
    def short_timeout(seconds):
        if seconds == 3:
            return asyncio_timeout(seconds)
        assert 64 < seconds <= 65
        return asyncio_timeout(0.15)

    monkeypatch.setattr(demo, "run_public_research", wait)
    monkeypatch.setattr(demo, "timeout", short_timeout)
    with ThreadPoolExecutor(2) as pool:
        calls = [pool.submit(research_client.post, RESEARCH, headers=research_attestation.headers(ANALYSIS), json=ANALYSIS)
                 for _ in started]
        try:
            assert all(event.wait(2) for event in started)
            assert research_client.get(CATALOG, headers=HEADERS).status_code == 200
            for call in calls:
                reply = call.result(timeout=2)
                assert reply.status_code == 504
                assert "X-MacroPulse-Research-Complete" not in reply.headers
            blocked = research_client.post(RESEARCH, headers=research_attestation.headers(ANALYSIS), json=ANALYSIS)
            assert blocked.status_code == 503 and blocked.headers["Retry-After"] == "1"
        finally:
            release.set()


def test_render_discards_serialization_that_reaches_the_deadline(research_client, monkeypatch, caplog, research_attestation):
    clock = [0]
    monkeypatch.setattr(demo, "monotonic", lambda: clock[0])

    def run(_settings, _params, _key, _model, deadline):
        assert deadline == 65
        clock[0] = 64

        def serialize():
            clock[0] = 65
            return '{"synthetic":"late"}'

        return SimpleNamespace(model_dump_json=serialize)

    monkeypatch.setattr(demo, "run_public_research", run)
    headers = research_attestation.headers(ANALYSIS, research_id=RESEARCH_ID)
    reply = research_client.post(RESEARCH, headers=headers, json=ANALYSIS)
    assert reply.status_code == 504
    assert reply.json() == {"detail": "Public Research timed out"}
    assert "X-MacroPulse-Research-Complete" not in reply.headers
    records = {record["stage"]: record for record in (
        json.loads(entry.message) for entry in caplog.records if entry.name == "uvicorn.error.macropulse.research"
    )}
    for stage in ("fastapi.serialize", "fastapi.deadline"):
        assert records[stage]["research_id"] == RESEARCH_ID
        assert records[stage]["error_type"] == "TimeoutError"


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
def test_public_research_uses_only_verified_demo_and_cites_evidence(demo_env, monkeypatch, shared, research_attestation):
    question = "How did the war in Ukraine relate to euro-area inflation?"
    connections = []
    transaction_open = False
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
        nonlocal transaction_open
        assert settings.shared_project is shared
        assert conninfo_to_dict(settings.dsn)["host"] == f"db.{ref}.supabase.co"
        connections.append(settings)
        transaction_open = True
        try:
            yield connection
        finally:
            transaction_open = False

    def read(db, item, window, *, max_points):
        assert transaction_open
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

    invalid_plan = False

    def simulate(request):
        assert not transaction_open
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
            answer = {"paragraphs": [{"statement_id": row["statement_id"], "style": "direct"} for row in rows]}
            if invalid_plan:
                answer["paragraphs"][0]["text"] = "The war caused 99% inflation."
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
        reply = research_client.post(RESEARCH, headers=research_attestation.headers(params), json=params)
        assert len(connections) == 1 and len(requests) == 4
        invalid_plan = True
        requests.clear()
        fallback = research_client.post(RESEARCH, headers=research_attestation.headers(params), json=params)
        assert fallback.status_code == 200 and len(requests) == 4
        assert fallback.json()["narrative_kind"] == "deterministic"
        assert "99% inflation" not in fallback.text
        monkeypatch.setattr(demo, "read_history", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            psycopg.OperationalError("Synthetic DEMO failure"),
        ))
        requests.clear()
        failure = research_client.post(RESEARCH, headers=research_attestation.headers(params), json=params)
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
    assert len(connections) == 3 and len(requests) == 2


@pytest.mark.parametrize("failure,code", [("input limit", 503), ("provider failure", 503), ("timeout", 504)])
def test_public_gemini_fails_closed_after_bounded_interpret_failover(research_client, monkeypatch, caplog, failure, code, research_attestation):
    paths = []
    error_types = {"input limit": "ValueError", "provider failure": "HTTPStatusError", "timeout": "ReadTimeout"}
    caplog.set_level(logging.INFO, logger="uvicorn.error.macropulse.research")

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
    headers = research_attestation.headers(ANALYSIS, research_id=RESEARCH_ID)
    reply = research_client.post(RESEARCH, headers=headers, json=ANALYSIS)
    assert reply.status_code == code and "synthetic" not in reply.text
    assert demo.RESEARCH_ID_HEADER not in reply.headers
    assert len(paths) == (1 if failure == "input limit" else 2)
    assert all(path.endswith(":countTokens") for path in paths)
    events = [json.loads(entry.getMessage()) for entry in caplog.records
              if entry.name == "uvicorn.error.macropulse.research"]
    by_stage = {event["stage"]: event for event in events}
    assert by_stage["gemini.count_tokens"]["error_type"] == error_types[failure]
    if failure == "provider failure":
        assert by_stage["gemini.count_tokens"]["provider_http_status"] == 503
    else:
        assert all("provider_http_status" not in event for event in events)
    assert by_stage["langgraph.interpret_with_llm"]["outcome"] == "error"
    assert by_stage["fastapi.research_runner"]["outcome"] == "error"
    assert by_stage["fastapi.response"]["http_status"] == code
    assert all(event["research_id"] == RESEARCH_ID and event["duration_ms"] >= 0 for event in events)
    assert all(entry.exc_info is None for entry in caplog.records)
    for protected in ("synthetic private provider failure", "synthetic provider deadline", ANALYSIS["question"],
                      SECRET, PASSWORD, DSN, "192.0.2.3", "AIza" + "f" * 35):
        assert protected not in caplog.text


@pytest.mark.parametrize("provider_status", [400, 403, 429, 503])
@pytest.mark.parametrize("phase", ["interpret", "compose"])
def test_gemini_generation_logs_provider_status_and_safe_transient_fallback(
    research_client, monkeypatch, caplog, phase, provider_status,
    research_attestation,
):
    caplog.set_level(logging.INFO, logger="uvicorn.error.macropulse.research")
    params = {**ANALYSIS, "question": "How did exchange rates move in the selected period?"}
    intent = {"purpose": "descriptive", "requirements": [{
        "excerpt": params["question"], "indicator": "eur-usd", "operation": "trend",
        "geography": "bilateral", "measure": "spot_reference_rate",
    }]}

    def read(_connection, item, window, **_options):
        assert phase == "compose"
        observations = []
        for raw, value in (("2020-02-03", "1.1"), ("2020-02-04", "1.2")):
            period = parse_period(item.frequency, raw)
            observations.append(MacroObservation(
                period_raw=raw, period_start=period.start, period_end=period.end, effective_date=None,
                value=Decimal(value), status_kind="normal", status_raw=None,
            ))
        return MacroHistory(indicator=indicator_metadata(item), start=window.start, end=window.end,
                            updated_at=None, observations=observations)

    monkeypatch.setattr(demo, "read_history", read)
    calls = []

    def simulate(request):
        operation = request.url.path.rsplit(":", 1)[1]
        calls.append(operation)
        if operation == "countTokens":
            return httpx.Response(200, json={"totalTokens": 200})
        if phase == "compose" and len(calls) == 2:
            return httpx.Response(200, json={
                "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 60},
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(intent)}]}}],
            })
        request.url = request.url.copy_add_param("key", "synthetic-url-credential")
        return httpx.Response(provider_status, text="synthetic private provider body")

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(simulate), **options,
    ))
    headers = research_attestation.headers(params, research_id=RESEARCH_ID)
    reply = research_client.post(RESEARCH, headers=headers, json=params)
    recovered = phase == "compose" and provider_status in (429, 503)
    if recovered:
        assert reply.status_code == 200
        assert reply.headers["X-MacroPulse-Research-Complete"] == "true"
        payload = reply.json()
        assert payload["status"] == "answered"
        assert payload["narrative_kind"] == "deterministic" and payload["model_id"] is None
        assert payload["narrative"] and all(item["citations"] for item in payload["narrative"])
        assert any("Gemini drafting was unavailable" in item for item in payload["limitations"])
    else:
        assert reply.status_code == 503 and reply.json() == {"detail": "Public Research is unavailable"}
    assert demo.RESEARCH_ID_HEADER not in reply.headers
    interpret_failover = phase == "interpret" and provider_status in (429, 503)
    assert calls == ["countTokens", "generateContent"] * (
        2 if phase == "compose" or interpret_failover else 1
    )

    records = [entry for entry in caplog.records if entry.name == "uvicorn.error.macropulse.research"]
    events = [json.loads(entry.getMessage()) for entry in records]
    assert all(entry.exc_info is None for entry in records)
    provider_events = [event for event in events if "provider_http_status" in event]
    assert [event["stage"] for event in provider_events] == (
        ["gemini.generate_content", "gemini.generate_content", "gemini.interpret_failover",
         "gemini.interpret"] if interpret_failover
        else ["gemini.generate_content", f"gemini.{phase}"]
    )
    for event in provider_events:
        assert event["event"] == "public_research_stage"
        assert event["outcome"] == "error" and event["error_type"] == "HTTPStatusError"
        assert event["provider_http_status"] == provider_status
        assert event["research_id"] == RESEARCH_ID and event["duration_ms"] >= 0
    assert next(event for event in events if event["stage"] == "fastapi.response")["http_status"] == (200 if recovered else 503)
    for protected in (params["question"], "synthetic-url-credential", "synthetic private provider body",
                      "generativelanguage.googleapis.com", "AIza" + "f" * 35):
        assert protected not in caplog.text


def test_research_preload_failure_is_sanitized_and_preserves_disabled_endpoint(demo_env, monkeypatch, caplog, research_attestation):
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AIza" + "f" * 35)

    def fail_preload():
        raise RuntimeError("synthetic private preload data")

    monkeypatch.setattr(v2_retrieval, "lexical", fail_preload)
    app = demo.create_public_app(connect=lambda _settings: MagicMock())
    with TestClient(app) as client:
        reply = client.post(RESEARCH, headers=research_attestation.headers(ANALYSIS), json=ANALYSIS)
    assert reply.status_code == 503 and reply.json() == {"detail": "Public Research is unavailable"}
    events = [json.loads(entry.getMessage()) for entry in caplog.records
              if entry.name == "uvicorn.error.macropulse.research" and entry.levelno == logging.WARNING]
    assert [(event["stage"], event["error_type"]) for event in events] == [
        ("fastapi.research_preload", "RuntimeError"), ("fastapi.admission", "ResearchUnavailable"),
    ]
    assert events[0]["research_id"] is None and events[1]["research_id"]
    assert "synthetic private" not in caplog.text


def test_interpretation_503_uses_bounded_lite_failover_and_keeps_verified_result(
    research_client, monkeypatch, research_attestation,
):
    """One rejected primary generation can recover within the same admitted request."""
    question = "How did exchange rates move in the selected period?"
    params = {**ANALYSIS, "question": question}
    intent = {"purpose": "descriptive", "requirements": [{
        "excerpt": question, "indicator": "eur-usd", "operation": "trend",
        "geography": "bilateral", "measure": "spot_reference_rate",
    }]}
    calls = []

    def read(_connection, item, window, **_options):
        values = []
        for raw, value in (("2020-02-03", "1.1"), ("2020-02-04", "1.2")):
            period = parse_period(item.frequency, raw)
            values.append(MacroObservation(
                period_raw=raw, period_start=period.start, period_end=period.end, effective_date=None,
                value=Decimal(value), status_kind="normal", status_raw=None,
            ))
        return MacroHistory(indicator=indicator_metadata(item), start=window.start, end=window.end,
                            updated_at=None, observations=values)

    monkeypatch.setattr(demo, "read_history", read)
    monkeypatch.setattr(public_gemini.PublicGemini, "compose", lambda self, statements: (
        v2_service.Draft.model_validate({"paragraphs": [
            {"statement_id": statement["statement_id"], "style": "direct"} for statement in statements
        ]})
    ))

    def provider(request):
        calls.append((request.url.path, request.extensions["timeout"]["read"]))
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 200})
        if "gemini-3.6-flash" in request.url.path:
            return httpx.Response(503, text="synthetic temporary provider outage")
        assert "gemini-3.5-flash-lite" in request.url.path
        return httpx.Response(200, json={
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 60},
            "candidates": [{"finishReason": "STOP",
                            "content": {"parts": [{"text": json.dumps(intent)}]}}],
        })

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(provider), **options,
    ))
    reply = research_client.post(RESEARCH, headers=research_attestation.headers(params), json=params)
    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "answered"
    assert body["narrative"] and all(statement["citations"] for statement in body["narrative"])
    assert [path.split("/")[-1].split(":")[0] for path, _ in calls] == [
        "gemini-3.6-flash", "gemini-3.6-flash",
        "gemini-3.5-flash-lite", "gemini-3.5-flash-lite",
    ]
    assert [budget for _, budget in calls] == [20, 20, 10, 10]
