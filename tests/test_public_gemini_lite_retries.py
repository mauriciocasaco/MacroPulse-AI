"""Bounded Flash-Lite retry policy: real request shaping with synthetic HTTP responses."""

from datetime import date
from time import monotonic

import httpx
import pytest

from backend.macro_ai import public_gemini
from backend.macro_ai.models import ResearchRequest


QUESTION = "How did the COVID pandemic affect the EUR/USD rate?"
INTENT = {
    "event": "covid", "purpose": "descriptive", "requirements": [{
        "excerpt": QUESTION, "indicator": "eur-usd", "operation": "trend",
        "geography": "bilateral", "measure": "spot_reference_rate",
    }],
}
DRAFT = {"paragraphs": [{"statement_id": 1, "style": "direct"}]}
EVIDENCE = [{"statement_id": 1, "category": "official observation",
             "statement": "The official synthetic observation is verified."}]


def request():
    return ResearchRequest(
        question=QUESTION, start=date(2020, 2, 1), end=date(2020, 12, 31),
        indicator_id="eur-usd", event_id="covid",
    )


def model():
    return public_gemini.PublicGemini(
        api_key="AIza" + "l" * 35, model="gemini-3.5-flash-lite", deadline=monotonic() + 65,
    )


def stub_http(monkeypatch, generate):
    seen = []
    original_client = httpx.AsyncClient

    def provider(call):
        seen.append((call.url.path, call.extensions["timeout"]["read"]))
        if call.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 480})
        return generate(call)

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **options: original_client(transport=httpx.MockTransport(provider), **options),
    )
    return seen


def generation_response(payload):
    import json
    return httpx.Response(200, json={
        "usageMetadata": {"promptTokenCount": 480, "candidatesTokenCount": 100},
        "candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": json.dumps(payload)}],
        }}],
    })


def test_lite_retries_two_transient_failures_then_composes(monkeypatch):
    delays = []
    monkeypatch.setattr(public_gemini, "sleep", delays.append)
    monkeypatch.setattr(public_gemini, "uniform", lambda _a, _b: 0)
    counts = [0]

    def generate(call):
        counts[0] += 1
        if counts[0] == 1:
            return httpx.Response(503, text="synthetic provider unavailable")
        if counts[0] == 2:
            raise httpx.ReadTimeout("synthetic provider timeout")
        return generation_response(INTENT if counts[0] == 3 else DRAFT)

    seen = stub_http(monkeypatch, generate)
    gemini = model()
    assert gemini.interpret(request()).event == "covid"
    assert gemini.compose(EVIDENCE).paragraphs[0].statement_id == 1
    assert gemini._calls == 4  # 3 interpretation attempts and one optional composition
    assert delays == [0.2, 0.4]
    assert len(seen) == 8
    assert all("gemini-3.5-flash-lite" in path for path, _ in seen)
    assert [budget for _, budget in seen] == [8] * 6 + [10] * 2


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_lite_retries_transient_5xx_at_most_three(monkeypatch, status):
    monkeypatch.setattr(public_gemini, "sleep", lambda _: None)
    monkeypatch.setattr(public_gemini, "uniform", lambda _a, _b: 0)
    seen = stub_http(monkeypatch, lambda _: httpx.Response(status, text="synthetic"))
    gemini = model()
    with pytest.raises(httpx.HTTPStatusError) as exception:
        gemini.interpret(request())
    assert exception.value.response.status_code == status
    assert gemini._calls == 3 and len(seen) == 6


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_lite_does_not_retry_nontransient_http_errors(monkeypatch, status):
    monkeypatch.setattr(
        public_gemini, "sleep", lambda _: pytest.fail("No retry should be attempted"),
    )
    seen = stub_http(monkeypatch, lambda _: httpx.Response(status, text="synthetic"))
    gemini = model()
    with pytest.raises(httpx.HTTPStatusError) as exception:
        gemini.interpret(request())
    assert exception.value.response.status_code == status
    assert gemini._calls == 1 and len(seen) == 2


def test_lite_does_not_retry_invalid_input_token_count(monkeypatch):
    seen = []
    original = httpx.AsyncClient

    def provider(call):
        seen.append(call.url.path)
        return httpx.Response(200, json={
            "totalTokens": public_gemini.PREFLIGHT_TOKENS + 1,
        })

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **options: original(transport=httpx.MockTransport(provider), **options),
    )
    monkeypatch.setattr(
        public_gemini, "sleep", lambda _: pytest.fail("Invalid input must not retry"),
    )
    gemini = model()
    with pytest.raises(ValueError, match="Research prompt exceeds"):
        gemini.interpret(request())
    assert gemini._calls == len(seen) == 1


def test_lite_does_not_retry_when_insufficient_research_budget(monkeypatch):
    monkeypatch.setattr(public_gemini, "monotonic", lambda: 100)
    monkeypatch.setattr(
        public_gemini, "sleep", lambda _: pytest.fail("Insufficient budget for a retry"),
    )
    seen = stub_http(monkeypatch, lambda _: httpx.Response(503, text="synthetic"))
    gemini = public_gemini.PublicGemini(
        api_key="AIza" + "l" * 35, model="gemini-3.5-flash-lite", deadline=137,
    )
    with pytest.raises(httpx.HTTPStatusError):
        gemini.interpret(request())
    assert gemini._calls == 1 and len(seen) == 2


def test_lite_composition_and_general_are_not_retried(monkeypatch):
    monkeypatch.setattr(
        public_gemini, "sleep", lambda _: pytest.fail("No optional-generation retries"),
    )
    seen = stub_http(monkeypatch, lambda _: httpx.Response(503, text="synthetic"))
    gemini = model()
    with pytest.raises(httpx.HTTPStatusError):
        gemini.compose(EVIDENCE)
    assert gemini._calls == 1
    assert len(seen) == 2
    assert all(budget == 10 for _, budget in seen)


def test_lite_triple_timeout_uses_only_three_interpretations(monkeypatch):
    monkeypatch.setattr(public_gemini, "sleep", lambda _: None)
    monkeypatch.setattr(public_gemini, "uniform", lambda _a, _b: 0)
    seen = stub_http(
        monkeypatch, lambda _: (_ for _ in ()).throw(
            httpx.ReadTimeout("synthetic timeout")
        ),
    )
    gemini = model()
    with pytest.raises(TimeoutError):
        gemini.interpret(request())
    assert gemini._calls == 3 and len(seen) == 6
