import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from dataclasses import replace
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import httpx
import pytest

from backend import public_demo
from backend.origin_attestation import Attestation, origin_config


FIXTURE = json.loads((Path(__file__).parent / "fixtures/security/origin-attestation-v1.json").read_text())
SAMPLE = FIXTURE["vectors"][0]
PATH = SAMPLE["path"]
NOW = SAMPLE["durable_object"]["issuedAt"]
BODY = base64.b64decode(SAMPLE["body_base64"])
HEADERS = SAMPLE["headers"]
LEGACY = sha256(b"synthetic legacy credential").hexdigest()
ENV = {
    "MACROPULSE_RESEARCH_ORIGIN_KEYS": json.dumps(FIXTURE["origin_keys"]),
    "MACROPULSE_RESEARCH_VERIFY_KEYS": json.dumps(FIXTURE["verify_keys"]),
    "MACROPULSE_RESEARCH_VERIFY_ACTIVE_KID": FIXTURE["verify_keys"][0][0],
    "MACROPULSE_RESEARCH_HOSTNAME": "demo.example",
    "MARKETPULSE_DEMO_RESEARCH_ENABLED": "true",
    "MARKETPULSE_DEMO_GEMINI_API_KEY": "AIza" + "f" * 35,
}


@pytest.fixture
def origin(monkeypatch):
    from backend.macro_ai import v2_retrieval, v2_service

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(public_demo, "time", lambda: NOW / 1000)
    monkeypatch.setattr(v2_retrieval, "lexical", lambda: None)
    monkeypatch.setattr(v2_service, "workflow", lambda: None)
    runner = Mock(return_value=SimpleNamespace(model_dump_json=lambda: '{"authorized":true}'))
    parser = Mock(return_value=SimpleNamespace(question="How did GDP change?"))

    def app():
        return public_demo.create_public_app(
            settings=public_demo.DemoSettings("password=synthetic-fixture-password", LEGACY),
            connect=lambda _: nullcontext(), research_runner=runner, research_parser=parser,
        )

    return SimpleNamespace(app=app, runner=runner, parser=parser)


def refsign(headers, body):
    keys = dict(FIXTURE["origin_keys"])
    canonical = "\n".join((
        "macropulse/render-research/v1", headers["X-MacroPulse-Origin-Key-Id"], "POST", PATH, "",
        headers["Content-Type"], sha256(body).hexdigest(), headers["X-MacroPulse-Origin-Timestamp"],
        headers["X-MacroPulse-Origin-Nonce"], headers["X-MacroPulse-Origin-Scope"],
        headers["X-MacroPulse-Research-Expires"], headers["X-MacroPulse-Research-Id"],
        headers["X-MarketPulse-Client-IP"],
    )).encode("ascii")
    key = bytes.fromhex(keys[headers["X-MacroPulse-Origin-Key-Id"]])
    return {**headers, "X-MacroPulse-Origin-Signature": hmac.digest(key, canonical, "sha256").hex()}


def mock_transport(monkeypatch, handler):
    client = httpx.AsyncClient

    def callback_client(**options):
        assert options == {"follow_redirects": False, "trust_env": False, "verify": True, "timeout": 2.5}
        return client(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(httpx, "AsyncClient", callback_client)


def test_unsigned_tampered_ambiguous_and_stale_requests_never_call_the_consumer(origin, monkeypatch):
    def must_not_call(_):
        pytest.fail("unauthorized request reached the callback")

    mock_transport(monkeypatch, must_not_call)
    with TestClient(origin.app()) as client:
        for headers in (
            {},
            {public_demo.ORIGIN_HEADER: LEGACY, public_demo.CLIENT_IP_HEADER: "192.0.2.10",
             "X-MacroPulse-Research-Expires": HEADERS["X-MacroPulse-Research-Expires"]},
            {**HEADERS, "X-MacroPulse-Origin-Signature": "f" * 64},
            {**HEADERS, "X-MacroPulse-Origin-Key-Id": "retired"},
            {**HEADERS, "X-MacroPulse-Origin-Nonce": "not-a-uuid"},
            {**HEADERS, "X-MacroPulse-Origin-Timestamp": "    1784160000000"},
            {**HEADERS, "X-MacroPulse-Origin-Scope": "public, admin"},
            {**HEADERS, "X-MacroPulse-Origin-Scope": "admin"},
            {**HEADERS, "X-MacroPulse-Research-Id": FIXTURE["vectors"][1]["headers"]["X-MacroPulse-Research-Id"]},
            {**HEADERS, "X-MarketPulse-Client-IP": "192.0.2.5"},
            {**HEADERS, "X-MacroPulse-Research-Expires": str(NOW + 89_999)},
            refsign({**HEADERS, "X-MacroPulse-Origin-Timestamp": str(NOW - 10_001)}, BODY),
            refsign({**HEADERS, "X-MacroPulse-Origin-Timestamp": str(NOW + 2_001)}, BODY),
        ):
            assert client.post(PATH, headers=headers, content=BODY).status_code == 403
        for name, value in HEADERS.items():
            assert client.post(PATH, headers=[*HEADERS.items(), (name.swapcase(), value)],
                               content=BODY).status_code in (403, 415)
        assert client.post(PATH, headers=HEADERS, content=BODY + b" ").status_code == 403
        assert client.put(PATH, headers=HEADERS, content=BODY).status_code == 405
        assert client.post(PATH + "?x=1", headers=HEADERS, content=BODY).status_code == 400
        assert client.post("/macro/%61nalysis", headers=HEADERS, content=BODY).status_code == 400
        assert client.post(PATH + "/", headers=HEADERS, content=BODY).status_code == 403
    origin.parser.assert_not_called()
    origin.runner.assert_not_called()


def test_shared_persistent_verdict_limits_concurrent_render_apps_and_survives_restart(origin, monkeypatch, tmp_path):
    path = tmp_path / "consumption.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE grants(payload BLOB PRIMARY KEY, consumed INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO grants(payload) VALUES (?)", (SAMPLE["callback"]["body_ascii"].encode(),))
    concurrent = Barrier(2)

    def callback(request):
        assert str(request.url) == "https://demo.example" + SAMPLE["callback"]["path"]
        assert request.method == "POST"
        assert request.headers["cache-control"] == "no-store"
        for name, value in SAMPLE["callback"]["headers"].items():
            assert request.headers[name] == value
        if request.content == SAMPLE["callback"]["body_ascii"].encode() and not concurrent.broken:
            concurrent.wait(timeout=5)
        with closing(sqlite3.connect(path)) as db, db:
            rows = db.execute("UPDATE grants SET consumed=1 WHERE payload=? AND consumed=0 RETURNING payload",
                              (request.content,)).fetchall()
        return httpx.Response(204 if rows else 403)

    mock_transport(monkeypatch, callback)
    with TestClient(origin.app()) as first, TestClient(origin.app()) as second:
        # An attacker can sign with a leaked key, but the new commitment has no bound grant.
        changed = BODY.replace(b"GDP", b"CPI")
        assert first.post(PATH, headers=refsign(HEADERS, changed), content=changed).status_code == 403
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda client: client.post(PATH, headers=HEADERS, content=BODY), (first, second)))
        assert sorted(reply.status_code for reply in responses) == [200, 403]
    concurrent.abort()
    with TestClient(origin.app()) as restarted:
        assert restarted.post(PATH, headers=HEADERS, content=BODY).status_code == 403
    origin.parser.assert_called_once()
    origin.runner.assert_called_once()


@pytest.mark.parametrize("status", [200, 301, 429, 500, 503, None])
def test_callback_failure_is_closed_without_retry(origin, monkeypatch, status):
    def fail(_request):
        if status is None:
            raise httpx.ConnectError("synthetic TLS or connection error")
        return httpx.Response(status, headers={"Location": "https://untrusted.example"} if status == 301 else {})

    callback = Mock(side_effect=fail)
    mock_transport(monkeypatch, callback)
    with TestClient(origin.app()) as client:
        response = client.post(PATH, headers=HEADERS, content=BODY)
    assert response.status_code == 503
    callback.assert_called_once()
    origin.parser.assert_not_called()
    origin.runner.assert_not_called()


def test_rotating_origin_and_callback_keys(origin, monkeypatch):
    calls = []

    def callback(request):
        calls.append(request.headers["X-MacroPulse-Verify-Key-Id"])
        return httpx.Response(204)

    mock_transport(monkeypatch, callback)
    second = FIXTURE["vectors"][1]
    cases = (SAMPLE, second)
    for active_index in range(2):
        monkeypatch.setenv("MACROPULSE_RESEARCH_VERIFY_ACTIVE_KID", FIXTURE["verify_keys"][active_index][0])
        with TestClient(origin.app()) as client:
            for vector in cases:
                assert client.post(PATH, headers=vector["headers"], content=base64.b64decode(
                    vector["body_base64"],
                )).status_code == 200
    monkeypatch.setenv("MACROPULSE_RESEARCH_ORIGIN_KEYS", json.dumps(FIXTURE["origin_keys"][1:]))
    with TestClient(origin.app()) as client:
        assert client.post(PATH, headers=HEADERS, content=BODY).status_code == 403
    assert calls == [FIXTURE["verify_keys"][0][0]] * 2 + [FIXTURE["verify_keys"][1][0]] * 2


@pytest.mark.parametrize("config_change", [
    {"MACROPULSE_RESEARCH_VERIFY_KEYS": "[]"},
    {"MACROPULSE_RESEARCH_VERIFY_KEYS": ENV["MACROPULSE_RESEARCH_ORIGIN_KEYS"]},
    {"MACROPULSE_RESEARCH_VERIFY_ACTIVE_KID": "unknown"},
    {"MACROPULSE_RESEARCH_HOSTNAME": "demo.example@untrusted.example"},
])
def test_misconfiguration_disables_research_without_affecting_reads(origin, monkeypatch, config_change):
    for key, value in config_change.items():
        monkeypatch.setenv(key, value)
    with TestClient(origin.app()) as client:
        assert client.post(PATH, headers=HEADERS, content=BODY).status_code == 503
        reply = client.head("/macro/indicators", headers={
            public_demo.ORIGIN_HEADER: LEGACY, public_demo.CLIENT_IP_HEADER: "192.0.2.10",
        })
    assert reply.status_code == 200
    assert public_demo.RESEARCH_HEADER not in reply.headers
    origin.runner.assert_not_called()


def test_shared_signatures_and_exact_clock_boundaries():
    config = origin_config(ENV, LEGACY)
    for vector in FIXTURE["vectors"]:
        headers = vector["headers"]
        grant = vector["durable_object"]
        attestation = Attestation(
            headers["X-MacroPulse-Origin-Key-Id"], grant["issuedAt"], grant["id"], headers["X-MacroPulse-Origin-Scope"],
            grant["expires"], headers["X-MacroPulse-Research-Id"], headers["X-MarketPulse-Client-IP"],
            bytes.fromhex(headers["X-MacroPulse-Origin-Signature"]),
        )
        body = base64.b64decode(vector["body_base64"])
        for vector_time in FIXTURE["freshness_vectors"]:
            assert attestation.authenticate(config, body, vector_time["now_ms"]) == (
                grant["commitment"] if vector_time["fresh"] else None
            )
        assert replace(attestation, expires=grant["expires"] - 1).authenticate(config, body, NOW) is None
