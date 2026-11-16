"""Phase 12.S2 adversarial tests across the attested API and real Research graph."""

from contextlib import nullcontext
import json
from pathlib import Path
from time import monotonic

from fastapi.testclient import TestClient
import httpx
import psycopg
import pytest

from backend import public_demo, public_online
from backend.macro_ai import online_evidence, public_gemini, v2_service
from backend.macro_ai.v2_models import CONCEPTUAL_FRAGMENTS
from backend.macro_data.api import MacroHistory, indicator_metadata


CONTRACT = json.loads((Path(__file__).parent / "fixtures/security/fase-12-s2.json").read_text(encoding="utf-8"))
POLICY = "unsupported_operation_or_series"
MODEL = "gemini-3.6-flash"
CONCEPTS = [(question, anchor) for question, kind, *ids in CONTRACT["questions"]
            if kind == "conceptual" for anchor in ids]
DESCRIPTIVE_QUESTION = "How much did the pandemic affect the euro exchange rate?"
DESCRIPTIVE = {
    "question": DESCRIPTIVE_QUESTION, "start": "2020-02-01", "end": "2020-12-31",
    "indicator_id": "eur-usd", "event_id": "covid", "research_version": "v2",
}
TECHNICAL_CASES = [
    ("malformed", "{", "STOP", "invalid_response"),
    ("duplicate_json_member", '{"fragment_ids":["inflation"],"fragment_ids":["inflation"]}', "STOP", "invalid_response"),
    ("truncated", {"fragment_ids": ["inflation"]}, "MAX_TOKENS", "invalid_response"),
    ("blocked", {"fragment_ids": ["inflation"]}, "SAFETY", "invalid_response"),
    ("outage", httpx.ConnectError("synthetic provider outage"), "STOP", "provider_unavailable"),
    ("timeout", httpx.ReadTimeout("synthetic provider timeout"), "STOP", "timeout"),
]


def simulate_provider(monkeypatch, *replies, finish="STOP"):
    calls = []

    async def post(_client, endpoint, payload):
        calls.append((endpoint, payload))
        if endpoint.endswith(":countTokens"):
            return {"totalTokens": 480}
        index = sum(name.endswith(":generateContent") for name, _ in calls) - 1
        reply = replies[index]
        if isinstance(reply, Exception):
            raise reply
        return {
            "usageMetadata": {"promptTokenCount": 480, "candidatesTokenCount": 120},
            "candidates": [{"finishReason": finish, "content": {
                "parts": [{"text": reply if isinstance(reply, str) else json.dumps(reply)}],
            }}],
        }

    monkeypatch.setattr(public_gemini.PublicGemini, "_post", staticmethod(post))
    return calls


def no_work(*_args, **_kwargs):
    pytest.fail("This research path attempted unauthorized work")


@pytest.fixture
def app(monkeypatch, research_attestation):
    monkeypatch.setenv(public_demo.GENERAL_FALLBACK_FLAG, "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AIza" + "f" * 35)
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_MODEL", MODEL)
    monkeypatch.delenv("MACROPULSE_ONLINE_DYNAMIC_RAG", raising=False)
    monkeypatch.setattr(public_online, "connect_online", no_work)
    monkeypatch.setattr(public_demo, "connect_demo", no_work)
    monkeypatch.setattr(v2_service.v2_retrieval, "retrieve", no_work)
    settings = public_demo.DemoSettings("password=synthetic-db-secret", "synthetic-origin-secret")
    return TestClient(public_demo.create_public_app(
        settings=settings, connect=lambda _: nullcontext(),
        research_runner=public_online.run_online_research, research_parser=public_online.parse_online_research,
    ))


def submit(app, attestation, question):
    response = attestation.post(app, {**DESCRIPTIVE, "question": question})
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-MacroPulse-Research-Complete"] == "true"
    assert len(response.content) < public_demo.MAX_RESEARCH_RESPONSE
    return response.json()


@pytest.mark.parametrize("question,anchor", CONCEPTS)
def test_useful_conceptual_explanations_use_one_generation_and_no_untrusted_input(
    app, monkeypatch, research_attestation, question, anchor,
):
    calls = simulate_provider(monkeypatch, {"fragment_ids": [anchor]})
    body = submit(app, research_attestation, question)
    assert body["support"] == "insufficient"
    assert body["insufficiency_reason"] == POLICY
    assert body["facts"] == body["evidence"] == body["narrative"] == body["observed_changes"] == []
    assert body["general_fallback"] == {
        "status": "answered", "verified": False, "triggered": True, "reason": POLICY, "model_id": MODEL,
        "answer": CONCEPTUAL_FRAGMENTS[anchor], "fragment_ids": [anchor], "catalog_version": "v1",
    }
    assert body["graph_steps"] == ["assess_conceptual_support", "finish", "generate_general_fallback"]
    assert [name.rsplit(":", 1)[-1] for name, _ in calls] == ["countTokens", "generateContent"]
    prompt = calls[-1][1]
    assert set(prompt) == {"systemInstruction", "contents", "generationConfig"}
    text = prompt["contents"][0]["parts"][0]["text"]
    assert question not in text and "2020-02-01" not in text and "synthetic" not in text
    fragments = json.loads(text)
    assert fragments[0] == {"id": anchor, "text": CONCEPTUAL_FRAGMENTS[anchor]}
    config = prompt["generationConfig"]
    assert config["maxOutputTokens"] == public_gemini.GENERAL_OUTPUT_TOKENS
    enum = config["responseJsonSchema"]["properties"]["fragment_ids"]["items"]["enum"]
    assert enum == [item["id"] for item in fragments]


@pytest.mark.parametrize("name,payload,finish,error", [
    *((name, value, "STOP", "invalid_response") for name, value in CONTRACT["provider"]),
    *TECHNICAL_CASES,
], ids=[name for name, _ in CONTRACT["provider"]] + [item[0] for item in TECHNICAL_CASES])
def test_adversarial_provider_response_preserves_the_public_rag_body(
    app, monkeypatch, research_attestation, name, payload, finish, error,
):
    monkeypatch.delenv(public_demo.GENERAL_FALLBACK_FLAG)
    empty = simulate_provider(monkeypatch)
    before = submit(app, research_attestation, "What is inflation?")
    assert empty == []
    monkeypatch.setenv(public_demo.GENERAL_FALLBACK_FLAG, "true")
    calls = simulate_provider(monkeypatch, payload, finish=finish)
    body = submit(app, research_attestation, "What is inflation?")
    assert body.pop("general_fallback") == {
        "status": "failed", "error": error, "verified": False, "triggered": True, "reason": POLICY, "model_id": MODEL,
    }, name
    assert body["graph_steps"].pop() == "generate_general_fallback"
    assert body == before
    assert len(calls) == 2


@pytest.mark.parametrize("tamper", [
    {"operation": "other"},
    {"operation": "causal_effect"},
    {"indicator": "deposit-rate"},
    {"measure": "annual_growth"},
    {"geography": "United States"},
    {"unit": "percent"},
    {"excerpt": "invented excerpt"},
    {"period": {"start": "2025-01-01", "end": "2025-01-31"}},
])
def test_interpreter_cannot_authorize_fallback(app, monkeypatch, research_attestation, tamper):
    calls = simulate_provider(monkeypatch, {
        "purpose": "causal", "requirements": [{
            "excerpt": DESCRIPTIVE_QUESTION, "indicator": "eur-usd", "operation": "trend",
            "geography": "bilateral", "measure": "spot_reference_rate", **tamper,
        }],
    })
    if tamper.get("operation") == "causal_effect":
        monkeypatch.setattr(public_online, "connect_online", lambda *_: nullcontext(object()))
        monkeypatch.setattr(online_evidence, "retrieve_online", lambda *_: [])
        monkeypatch.setattr(v2_service.v2_retrieval, "retrieve", lambda *_, **_options: [])
    body = submit(app, research_attestation, DESCRIPTIVE_QUESTION)
    assert body["status"] == "insufficient_evidence"
    assert "general_fallback" not in body
    assert body["evidence"] == body["narrative"] == []
    assert len(calls) == 2


def test_missing_observations_stay_rag_only(app, monkeypatch, research_attestation):
    calls = simulate_provider(monkeypatch, {
        "purpose": "descriptive", "requirements": [{
            "excerpt": DESCRIPTIVE_QUESTION, "indicator": "eur-usd", "operation": "trend",
            "geography": "bilateral", "measure": "spot_reference_rate",
        }],
    })
    monkeypatch.setattr(public_online, "connect_online", lambda *_: nullcontext(object()))
    monkeypatch.setattr(online_evidence, "retrieve_online", lambda *_: [])
    monkeypatch.setattr(v2_service.v2_retrieval, "retrieve", lambda *_, **_options: [])
    monkeypatch.setattr(public_online, "load_history", lambda _conn, item, window, **_options: MacroHistory(
        indicator=indicator_metadata(item), start=window.start, end=window.end, updated_at=None, observations=[],
    ))
    body = submit(app, research_attestation, DESCRIPTIVE_QUESTION)
    assert body["insufficiency_reason"] == "missing_official_observations"
    assert "general_fallback" not in body and len(calls) == 2


def test_sql_exception_stays_a_service_failure(app, monkeypatch, research_attestation):
    calls = simulate_provider(monkeypatch, {
        "purpose": "descriptive", "requirements": [{
            "excerpt": DESCRIPTIVE_QUESTION, "indicator": "eur-usd", "operation": "trend",
            "geography": "bilateral", "measure": "spot_reference_rate",
        }],
    })

    def fail(_settings):
        raise psycopg.OperationalError("synthetic internal SQL failure")

    monkeypatch.setattr(public_online, "connect_online", fail)
    result = research_attestation.post(app, DESCRIPTIVE)
    assert result.status_code == 503
    assert result.json() == {"detail": "Public Research is unavailable"}
    assert len(calls) == 2


def test_interpretation_failover_exhaustion_stays_a_service_failure(app, monkeypatch, research_attestation):
    calls = simulate_provider(monkeypatch, httpx.ConnectError("synthetic primary outage"),
                              httpx.ConnectError("synthetic alternate outage"))
    response = research_attestation.post(app, DESCRIPTIVE)
    assert response.status_code == 503
    assert response.json() == {"detail": "Public Research is unavailable"}
    assert [name for name, _ in calls if name.endswith(":generateContent")] == [
        "models/gemini-3.6-flash:generateContent", "models/gemini-3.5-flash-lite:generateContent",
    ]


def test_conceptual_coverage_gap_without_budget_is_only_rag_insufficiency(monkeypatch):
    request = public_demo.parse_research(json.dumps({**DESCRIPTIVE, "question": "What is inflation?"}).encode())
    provider = public_gemini.PublicGemini(api_key="fixture", model=MODEL, deadline=monotonic() + 1)
    runtime = v2_service.ResearchRuntime(
        no_work, no_work, no_work, MODEL, generate_general=no_work, general_budget=provider.can_generate_general,
    )
    monkeypatch.setattr(public_gemini.PublicGemini, "_post", no_work)
    body = v2_service.run(request, runtime=runtime)
    assert body.insufficiency_reason == POLICY and body.general_fallback is None

    provider = public_gemini.PublicGemini(api_key="fixture", model=MODEL, deadline=monotonic() + 40)
    provider._calls = public_gemini.MAX_GENERATIONS
    assert not provider.can_generate_general()
