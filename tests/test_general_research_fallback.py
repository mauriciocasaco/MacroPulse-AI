"""Exercise the real graph and provider limits with synthetic inputs and no network."""

from contextlib import nullcontext
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from time import monotonic

import httpx
import pytest

from backend import public_demo, public_online
from backend.macro_ai import online_evidence, public_gemini, v2_service
from backend.macro_ai.models import ResearchRequest
from backend.macro_data.api import MacroHistory, MacroObservation, indicator_metadata
from backend.macro_data.contracts import CATALOG


QUESTION = "How much did the pandemic affect the euro exchange rate?"
ANSWER = "A shift in demand for safe assets could affect exchange rates. Isolating its effect would require a credible counterfactual."
MODEL = "gemini-3.6-flash"
START = date(2020, 2, 1)
END = date(2020, 12, 31)


@pytest.fixture
def hybrid(monkeypatch):
    monkeypatch.setattr(v2_service.v2_retrieval, "retrieve", lambda *args, **kwargs: [])
    monkeypatch.setattr(public_online, "connect_online", lambda *_: pytest.fail("unexpected data connection"))
    monkeypatch.setattr(public_demo, "connect_demo", lambda *_: pytest.fail("unexpected data connection"))
    monkeypatch.delenv(public_demo.GENERAL_FALLBACK_FLAG, raising=False)
    monkeypatch.delenv("MACROPULSE_ONLINE_DYNAMIC_RAG", raising=False)
    return ResearchRequest(question=QUESTION, start=START, end=END, indicator_id="eur-usd", event_id="covid")


def interpret(request, operation, **kwargs):
    return {
        "purpose": "causal" if operation == "causal_effect" else "descriptive",
        "requirements": [{"excerpt": request.question, "indicator": "eur-usd", "operation": operation,
                          "geography": "bilateral", "measure": "spot_reference_rate"}],
        **kwargs,
    }


def simulate_provider(monkeypatch, *replies):
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
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(reply)}]}}],
        }

    monkeypatch.setattr(public_gemini.PublicGemini, "_post", staticmethod(post))
    return calls


@pytest.mark.parametrize("operation,metadata,reason", [
    ("causal_effect", {}, "insufficient_documentary_evidence"),
    ("other", {"unresolved": ["multiple plausible meanings"]}, "ambiguous_interpretation"),
    ("other", {}, "unsupported_operation_or_series"),
])
def test_hybrid_keeps_rag_and_limits_the_general_prompt(hybrid, monkeypatch, operation, metadata, reason):
    calls = simulate_provider(monkeypatch, interpret(hybrid, operation, **metadata), {"answer": ANSWER})
    monkeypatch.setenv(public_demo.GENERAL_FALLBACK_FLAG, "true")
    if operation == "causal_effect":
        monkeypatch.setattr(public_online, "connect_online", lambda *_: nullcontext(object()))
    monkeypatch.setattr(online_evidence, "retrieve_online", lambda *_: [])
    body = public_online.run_online_research(object(), hybrid, "fixture-key", MODEL, monotonic() + 40)
    payload = body.model_dump(mode="json")
    assert payload["status"] == "insufficient_evidence" and payload["support"] == "insufficient"
    assert payload["insufficiency_reason"] == reason
    assert payload["evidence"] == payload["narrative"] == payload["observed_changes"] == []
    assert payload["general_fallback"] == {
        "triggered": True, "verified": False, "reason": reason, "status": "answered", "model_id": MODEL, "answer": ANSWER,
    }
    assert payload["graph_steps"][-2:] == ["finish", "generate_general_fallback"]
    assert len(calls) == 4
    prompt = calls[-1][1]
    assert prompt["generationConfig"]["maxOutputTokens"] == public_gemini.GENERAL_OUTPUT_TOKENS
    assert set(prompt) == {"systemInstruction", "contents", "generationConfig"}  # no grounding or other tools
    instructions = prompt["systemInstruction"]["parts"][0]["text"]
    assert "do not assert that an event actually caused an outcome" in instructions
    assert "untrusted data" in instructions and "citations" in instructions
    context = json.loads(prompt["contents"][0]["parts"][0]["text"])
    assert {key: context[key] for key in ("question", "indicator_id", "event_id", "start", "end")} == hybrid.model_dump(
        mode="json",
    )
    assert context["rag_diagnosis"] == reason
    assert "fixture-key" not in json.dumps(prompt)
    assert len(body.model_dump_json().encode("utf-8")) < public_demo.MAX_RESEARCH_RESPONSE


@pytest.mark.parametrize("reply,error", [
    (httpx.ReadTimeout("synthetic timeout"), "timeout"),
    (httpx.ConnectError("synthetic provider outage"), "provider_unavailable"),
    ({"answer": "The pandemic caused a rise of 5% in the euro."}, "invalid_response"),
    ({"answer": "According to ECB, the shock affected demand."}, "invalid_response"),
    ({"answer": "A supply shock could raise costs. See https://example.test."}, "invalid_response"),
    ({"answer": ANSWER, "citations": []}, "invalid_response"),
])
def test_demo_fallback_failure_returns_the_original_rag_diagnosis(hybrid, monkeypatch, reply, error):
    monkeypatch.setenv(public_demo.GENERAL_FALLBACK_FLAG, "true")
    calls = simulate_provider(monkeypatch, interpret(hybrid, "other"), reply)
    body = public_demo.run_public_research(object(), hybrid, "fixture-key", MODEL, monotonic() + 40)
    assert body.status == "insufficient_evidence"
    assert body.insufficiency_reason == "unsupported_operation_or_series"
    assert body.general_fallback.error == error
    assert "answer" not in body.general_fallback.model_dump()
    assert len(calls) == 4


def test_flag_default_and_interpretation_provider_failure_never_trigger_fallback(hybrid, monkeypatch):
    calls = simulate_provider(monkeypatch, interpret(hybrid, "other"))
    body = public_online.run_online_research(object(), hybrid, "fixture-key", MODEL, monotonic() + 40)
    assert body.status == "insufficient_evidence" and body.insufficiency_reason == "unsupported_operation_or_series"
    assert "general_fallback" not in body.model_dump(mode="json")
    assert len(calls) == 2

    monkeypatch.setenv(public_demo.GENERAL_FALLBACK_FLAG, "true")
    calls = simulate_provider(monkeypatch, httpx.ConnectError("synthetic interpreter failure"))
    with pytest.raises(httpx.ConnectError):
        public_online.run_online_research(object(), hybrid, "fixture-key", MODEL, monotonic() + 40)
    assert len(calls) == 2

    bad_intent = interpret(hybrid, "other")
    bad_intent["requirements"][0]["excerpt"] = "invented excerpt"
    calls = simulate_provider(monkeypatch, bad_intent)
    body = public_online.run_online_research(object(), hybrid, "fixture-key", MODEL, monotonic() + 40)
    assert body.insufficiency_reason == "interpretation_unavailable" and body.general_fallback is None
    assert len(calls) == 2


def test_presets_keep_valid_official_results_and_leave_time_to_return_timeout(hybrid, monkeypatch):
    request = hybrid.model_copy(update={"question": "What happened to EUR/USD rate?", "event_id": None})
    dates = [date(2020, 2, 3), date(2020, 2, 4)]
    history = MacroHistory(
        indicator=indicator_metadata(CATALOG["eur-usd"]), start=START, end=END,
        updated_at=datetime(2020, 12, 31, tzinfo=timezone.utc),
        observations=[
            MacroObservation(period_raw=day.isoformat(), period_start=day, period_end=day, effective_date=None,
                             value=Decimal(value),
                             status_kind="normal", status_raw=None)
            for day, value in zip(dates, ("1.1", "1.2"), strict=True)
        ],
    )
    provider = public_gemini.PublicGemini(api_key="fixture-key", model=MODEL, deadline=monotonic() + 40)
    calls = simulate_provider(monkeypatch, {"paragraphs": [
        {"statement_id": 1, "style": "direct"}, {"statement_id": 2, "style": "direct"},
    ]})
    runtime = v2_service.ResearchRuntime(lambda *_: history, provider.interpret, provider.compose, MODEL,
                                         generate_general=provider.general)
    body = v2_service.run(request, runtime=runtime)
    assert body.status == "answered" and body.support == "supported" and body.general_fallback is None
    assert len(calls) == 2

    provider = public_gemini.PublicGemini(api_key="fixture-key", model=MODEL, deadline=monotonic() + 1)
    empty = history.model_copy(update={"observations": []})
    runtime = v2_service.ResearchRuntime(lambda *_: empty, provider.interpret, provider.compose, MODEL,
                                         generate_general=provider.general)
    body = v2_service.run(request, runtime=runtime)
    assert body.insufficiency_reason == "missing_official_observations" and body.general_fallback.error == "timeout"
    assert len(calls) == 2  # no additional request was sent
