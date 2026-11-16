"""Capture both provider operations with synthetic HTTP and audit their logs."""

from datetime import date
import json
import logging
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx
import pytest

from backend.macro_ai import public_gemini
from backend.macro_ai.models import ResearchRequest
from backend.macro_ai.sensitive_input import SensitiveInputError, configured_sensitive_values
from backend.research_logging import research_id, research_stage


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/security/fase-12-s3.json").read_text(encoding="utf-8")
)
FLASH = "gemini-3.6-flash"
LITE = "gemini-3.5-flash-lite"
KEY = "AIza" + "p" * 35
PROVIDER_ERROR = "synthetic private contents of provider failure"
ANSWER = "Exchange-rate changes can feed through to import prices depending on the invoicing currency."


@pytest.fixture
def transport(monkeypatch, caplog):
    seen = []
    replies = []
    client = httpx.AsyncClient

    def simulate(request):
        seen.append((request.url.path, json.loads(request.content)))
        assert request.headers["x-goog-api-key"] == KEY
        assert not request.url.query
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 201})
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, int):
            return httpx.Response(response, text=PROVIDER_ERROR)
        return httpx.Response(200, json={
            "usageMetadata": {"promptTokenCount": 201, "candidatesTokenCount": 50},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(response)}]}}],
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(simulate), **options,
    ))
    monkeypatch.setattr(public_gemini, "sleep", lambda _: None)
    caplog.set_level(logging.INFO)
    token = research_id.set(str(uuid4()))
    try:
        yield seen, replies
    finally:
        research_id.reset(token)


def _request(question):
    return ResearchRequest(question=question, start=date(2020, 2, 1), end=date(2020, 12, 31),
                           indicator_id="hicp-yoy", event_id="covid")


def _gemini(model=FLASH):
    return public_gemini.PublicGemini(api_key=KEY, model=model, deadline=monotonic() + 65)


def _intent(question):
    return {"purpose": "descriptive", "requirements": [{
        "excerpt": question, "indicator": "hicp-yoy", "operation": "trend",
        "geography": "euro_area", "measure": "annual_inflation",
    }]}


def _stage_payloads(requests, *models):
    assert len(requests) == 2 * len(models)
    for index, model in enumerate(models):
        (count_path, count), (generate_path, generate) = requests[2 * index:2 * index + 2]
        assert count_path.endswith(f"/{model}:countTokens")
        assert generate_path.endswith(f"/{model}:generateContent")
        assert count == {"generateContentRequest": {"model": f"models/{model}", **generate}}
        assert KEY not in json.dumps(generate)
        yield generate["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize("case", [case for case in FIXTURE["questions"]["admitted"]
                                  if case["id"] in {"normal", "complex"}], ids=lambda case: case["id"])
def test_interpretation_and_rag_allow_only_stage_fields(transport, caplog, case):
    seen, replies = transport
    fixture = FIXTURE["provider"]
    question = "".join(case["parts"])
    replies.extend([_intent(question), {"paragraphs": [
        {"statement_id": row["statement_id"], "style": "direct"} for row in fixture["statements"]
    ]}])
    gemini = _gemini()
    request = _request(question)
    gemini.interpret(request, knowledge_cutoff=date(2020, 6, 30))
    gemini.compose([
        dict(row, source_url=fixture["private_url"], private_id=fixture["private_id"],
             internal=fixture["private_marker"], row={"password": "fictional"})
        for row in fixture["statements"]
    ])
    interpretation, composition = _stage_payloads(seen, FLASH, FLASH)
    scope_text, original = interpretation.split("\n\nOriginal question:\n")
    scope = json.loads(scope_text.removeprefix("Catalog and allowed filters:\n"))
    assert original == question
    assert scope["knowledge_cutoff"] == "2020-06-30"
    assert set(scope) == {"catalog", "events", "filters", "knowledge_cutoff", "cutoff_instruction"}
    assert set(scope["filters"]) == {"start", "end", "indicator_id", "event_id"}
    assert json.loads(composition.removeprefix("Authorized statements:\n")) == fixture["statements"]
    assert question not in caplog.text
    for item in (KEY, fixture["private_marker"], fixture["private_url"], fixture["private_id"]):
        assert item not in caplog.text and item not in composition and item not in interpretation


def test_pure_llm_retains_free_generation_and_minimal_context(transport, caplog):
    seen, replies = transport
    request = _request("".join(FIXTURE["questions"]["admitted"][-1]["parts"]))
    replies.append({"answer": ANSWER})
    assert _gemini().general(request, "unsupported_operation_or_series", event="covid", knowledge_cutoff=None).answer == ANSWER
    message, = _stage_payloads(seen, FLASH)
    assert json.loads(message) == {
        "question": request.question, "start": "2020-02-01", "end": "2020-12-31",
        "indicator_id": "hicp-yoy", "event_id": "covid", "interpreted_event": "covid",
        "knowledge_cutoff": None, "rag_diagnosis": "unsupported_operation_or_series",
    }
    assert request.question not in caplog.text and ANSWER not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("model,errors,models", [
    (FLASH, (503,), (FLASH, LITE)),
    (LITE, (503, httpx.ReadTimeout(PROVIDER_ERROR)), (LITE, LITE, LITE)),
])
def test_failover_and_retry_keep_the_same_minimized_payload(transport, caplog, model, errors, models):
    seen, replies = transport
    request = _request("How does monetary policy affect inflation?")
    replies.extend([*errors, _intent(request.question)])
    _gemini(model).interpret(request)
    assert len(set(_stage_payloads(seen, *models))) == 1
    assert PROVIDER_ERROR not in caplog.text and KEY not in caplog.text and request.question not in caplog.text


@pytest.mark.parametrize("case", FIXTURE["questions"]["sensitive"], ids=lambda case: case["id"])
def test_provider_boundary_rejects_sensitive_content_without_counting(transport, caplog, case):
    seen, _ = transport
    question = "".join(case["parts"])
    token = configured_sensitive_values.set((case.get("configured_secret", ""),))
    try:
        for model in (FLASH, LITE):
            gemini = _gemini(model)
            if "\n" not in question:
                with pytest.raises(SensitiveInputError):
                    gemini.interpret(_request(question))
                with pytest.raises(SensitiveInputError):
                    gemini.general(_request(question), "unsupported_operation_or_series", event=None, knowledge_cutoff=None)
            with pytest.raises(SensitiveInputError):
                gemini.compose([{"statement_id": 1, "category": "documentary context", "statement": question}])
    finally:
        configured_sensitive_values.reset(token)
    assert seen == []
    assert question not in caplog.text and KEY not in caplog.text


def test_research_logs_drop_unrecognized_exception_names(transport, caplog):
    name = "SyntheticPrivateValue"
    error = type(name, (RuntimeError,), {})("synthetic private argument")
    with pytest.raises(RuntimeError), research_stage("gemini.validate_schema"):
        raise error
    record = json.loads(caplog.records[-1].getMessage())
    assert record["error_type"] == "UnknownError"
    assert name not in caplog.text and str(error) not in caplog.text
