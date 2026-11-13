"""HTTP and graph integration with synthetic observations and a simulated Gemini."""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.genai.models import Models
from google.genai.types import Candidate, Content, GenerateContentResponse, Part
import pytest

from backend.api import app
from backend.macro_ai import api, gemini, service, v2_gemini, v2_retrieval, v2_service
from backend.macro_ai.models import EVENT_WINDOWS, ResearchResponse
from backend.macro_ai.v2_models import Draft, Intent
from backend.macro_ai.v2_statistics import DEFINITIONS
from backend.macro_data import api as data
from backend.macro_data.contracts import CATALOG, parse_period


UKRAINE = "How did the war in Ukraine relate to euro-area inflation?"
DIFFERENCE = "How much did euro-area annual inflation increase between September and October 2022?"
COVID_GDP = "What happened to euro-area GDP during the COVID-19 shock?"
GENAI = "How much did generative AI increase euro-area GDP?"
FX = "What happened to EUR/USD during the pandemic?"
HICP_RELEASE = "0fe46a774051990653d228355faf94289d1bb9366bc3ded512da649a92db3dba"
ECB_ASSESSMENT = "571dc00d4eed3f3a920020c8c12e969b6cb430f661afb0a5b4f0027dec3ae41e"
GDP_RELEASE = "dd362d35d0c18a73ae4e95559e824d8b4832a38b6468b731407acc16516ba03b"
AI_ADOPTION = "23c63995ed81f8058b13491026e1f141e2ca01035bc76f16a15ce97320ef82e7"
COVID_HICP = "How did the COVID-19 pandemic affect Eurozone Inflation?"
COVID_HICP_CLAIMS = {
    "811e23f073b7f0b5f6b14fb6fdea80f4179bbabeefdf28e66794b040e78d22b9",
    "7f313f3f814598234846ef8cad8f2212a1bf0c6d20d23268ac86c5e534c4cbd7",
}


def requirement(question, key, operation):
    definition = DEFINITIONS[key]
    return {"excerpt": question, "indicator": key, "operation": operation,
            "geography": definition.geography[0], "measure": definition.measure}


def window(start, end):
    return {"start": start, "end": end}


@pytest.fixture
def research(monkeypatch):
    monkeypatch.setattr(api, "_require_local", lambda _: None)
    monkeypatch.setitem(app.dependency_overrides, data._require_local, lambda: None)
    monkeypatch.setenv("MARKETPULSE_MODE", "local")
    monkeypatch.setenv("MARKETPULSE_MACRO_AI_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_RESEARCH_V2_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("MARKETPULSE_RESEARCH_V2_RETRIEVER", "tfidf")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    fixture = SimpleNamespace(calls=[], reads=[], intent=None, draft=None, unavailable=False, coverage={},
                              values={
                                  "hicp-yoy": [("2022-02", "5.9"), ("2022-08", "9.1"), ("2022-09", "9.9"),
                                               ("2022-10", "10.6"), ("2024-02", "2.6")],
                                  "real-gdp-qoq": [("2020-Q1", "-3.7"), ("2020-Q2", "-11.8"),
                                                   ("2020-Q3", "12.5"), ("2020-Q4", "-0.4")],
                                  "eur-usd": [("2020-02-03", "1.10"), ("2020-03-20", "1.07"),
                                              ("2020-12-31", "1.22")],
                              })

    def fake_gemini(prompt, inputs, schema):
        fixture.calls.append((schema, inputs))
        assert "synthetic-key" not in json.dumps(inputs)
        if schema is Intent:
            assert "Original question" in prompt.format(**inputs)
            if fixture.intent is None:
                raise TimeoutError("synthetic provider failure")
            return schema.model_validate(fixture.intent)
        assert schema is Draft
        payload = json.loads(inputs["evidence"])
        if fixture.draft:
            return fixture.draft(payload)
        return {"sentences": [{"evidence_id": row["evidence_id"], "text": row["sentences"][-1]} for row in payload]}

    def fake_read(keys, request):
        key, = keys
        fixture.reads.append((key, request.start.isoformat(), request.end.isoformat()))
        if fixture.unavailable:
            raise RuntimeError("synthetic reader failure")
        item = CATALOG[key]
        observations = []
        for period, value in fixture.values[key]:
            parsed = parse_period(item.frequency, period)
            observations.append(data.MacroObservation(
                period_raw=period, period_start=parsed.start, period_end=parsed.end,
                effective_date=parsed.effective_date, value=Decimal(value),
                status_kind="normal", status_raw=None,
            ))
        return {key: data.MacroHistory(indicator=data.indicator_metadata(item), start=request.start, end=request.end,
                                       updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                       observations=observations, **fixture.coverage)}

    monkeypatch.setattr(v2_gemini, "invoke_structured", fake_gemini)
    monkeypatch.setattr(v2_service, "read_statistics", fake_read)
    with TestClient(app) as client:
        fixture.client = client
        yield fixture


def post(research, question, key, event, *, v2=True, headers=None):
    start, end = EVENT_WINDOWS[event]
    return research.client.post("/macro/analysis", headers=headers, json={
        "question": question, "indicator_id": key, "event_id": event, **window(start.isoformat(), end.isoformat()),
        **({"research_version": "v2"} if v2 else {}),
    })


@pytest.mark.parametrize("question,key,event,operation,purpose,claims,expected", [
    (UKRAINE, "hicp-yoy", "ukraine", "trend", "causal", [HICP_RELEASE, ECB_ASSESSMENT], "partial"),
    (DIFFERENCE, "hicp-yoy", "ukraine", "difference", "descriptive", [], "supported"),
    (COVID_GDP, "real-gdp-qoq", "covid", "trend", "descriptive", [GDP_RELEASE], "supported"),
    (GENAI, "real-gdp-qoq", "generative-ai", "causal_effect", "causal", [AI_ADOPTION], "partial"),
    (FX, "eur-usd", "covid", "trend", "descriptive", [], "partial"),
])
def test_five_examples(research, question, key, event, operation, purpose, claims, expected):
    item = requirement(question, key, operation)
    if operation == "difference":
        item.update(unit="percentage_points", operands=[
            window("2022-09-01", "2022-09-30"), window("2022-10-01", "2022-10-31"),
        ])
    research.intent = {"event": None if question == DIFFERENCE else event, "purpose": purpose, "requirements": [item]}
    response = post(research, question, key, event)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["research_v2"] and body["support"] == expected
    assert body["narrative_kind"] == "gemini" and body["model_id"] == "gemini-3.6-flash"
    assert body["graph_steps"] == [
        "interpret_with_llm", "validate_scope", "read_official_statistics", "retrieve_curated_evidence",
        "evaluate_support", "compose_answer", "validate_answer", "finish",
    ]
    assert [schema for schema, _ in research.calls] == [Intent, Draft]
    assert research.calls[0][1]["question"] == question
    assert question not in research.calls[1][1]["evidence"]

    series = [] if question == GENAI else [f"series:{key}"]
    expected_sources = [*series, *(f"claim:{id}" for id in claims)]
    assert [item["id"] for item in body["evidence"]] == expected_sources
    assert {citation for claim in body["narrative"] for citation in claim["citations"]} == set(expected_sources)
    corpus = v2_retrieval.approved_corpus()
    for evidence in body["evidence"]:
        if evidence["kind"] == "series":
            assert evidence["url"] == CATALOG[key].source_url and evidence["series_key"] == CATALOG[key].series_key
            assert evidence["unit"] == CATALOG[key].unit and evidence["adjustment"] == CATALOG[key].adjustment
            continue
        claim = corpus.claims[evidence["claim_id"]]
        doc = corpus.documents[claim.document_id]
        assert evidence["document_id"] == doc.id
        assert evidence["url"] == claim.primary_source and evidence["locator"] == claim.locator
        assert evidence["qualifiers"] == claim.qualifiers and evidence["geography"] == claim.geography
        assert evidence["relation"] == doc.indicators[key] and evidence["indicator"] == key
        assert evidence["published_on"] == claim.publication_date <= body["publication_cutoff"]
        assert evidence["event"] == claim.event and evidence["claim_type"] == claim.type
        assert [row["text"] for row in body["interpretation"] if row["citations"] == [evidence["id"]]] == [claim.text]

    if question == UKRAINE:
        assert "energy and food prices" in body["interpretation"][-1]["text"]
        assert any("causal effect" in note for note in body["limitations"])
    elif question == DIFFERENCE:
        assert research.reads == [(key, "2022-09-01", "2022-10-31")]
        assert "9.9% year on year in 2022-09, 10.6% year on year in 2022-10" in body["facts"][0]["text"]
        assert "0.7 percentage points" in body["calculations"][0]["text"]
        assert body["observed_changes"][0]["value"] == "0.70"
    elif question == COVID_GDP:
        assert "-11.8% quarter on quarter in 2020-Q2" in body["facts"][0]["text"]
        assert "12.5% quarter on quarter in 2020-Q3" in body["facts"][0]["text"]
        assert "11.4 percentage points" in body["calculations"][0]["text"]
    elif question == GENAI:
        assert research.reads == body["facts"] == body["calculations"] == body["observed_changes"] == []
        assert body["evidence"][0]["relation"] == "proxy"
        assert body["narrative"][0]["category"] == "proxy context"
        assert any("no direct evidence" in note for note in body["limitations"])
    else:
        assert body["evidence"][0]["series_key"] == "EXR.D.USD.EUR.SP00.A"
        assert "10.909091%" in body["calculations"][0]["text"]
        assert any("no applicable documentary context" in note for note in body["limitations"])


@pytest.mark.parametrize("event,prefix,periods", [
    ("covid", "How did the COVID-19 pandemic affect", ("2020-02", "2020-12", "2020-Q2", "2020-Q4")),
    ("ukraine", "How did the war in Ukraine affect", ("2022-02", "2024-02", "2022-Q2", "2023-Q4")),
    ("generative-ai", "How did the rise of generative AI and LLMs affect",
     ("2023-09", "2024-03", "2023-Q4", "2024-Q1")),
])
@pytest.mark.parametrize("key,label,expected_unit,expected_value", [
    ("eur-usd", "EUR/USD rate", "percent", "-25.00"),
    ("m3-stock", "Eurozone M3", "percent", "-25.00"),
    ("deposit-rate", "ECB rate", "percentage_points", "0.00"),
    ("real-gdp-qoq", "Eurozone GDP", "percentage_points", "-0.40"),
    ("hicp-yoy", "Eurozone Inflation", "percentage_points", "-0.40"),
])
def test_preset_questions(research, event, prefix, periods, key, label, expected_unit, expected_value):
    question = f"{prefix} {label}?"
    start, end = EVENT_WINDOWS[event]
    first_month, last_month, first_quarter, last_quarter = periods
    if key == "deposit-rate":
        anchor_date = date(2019, 9, 18)
        research.coverage = {
            "coverage_start": start, "coverage_end": end,
            "anchor": data.MacroObservation(period_raw=anchor_date.isoformat(), period_start=anchor_date,
                                            period_end=anchor_date, effective_date=anchor_date,
                                            value=Decimal("-0.50"), status_kind="normal", status_raw=None),
        }
        research.values[key] = []
    else:
        first, last = {
            "eur-usd": (first_month + "-03", last_month + "-15"),
            "real-gdp-qoq": (first_quarter, last_quarter),
        }.get(key, (first_month, last_month))
        values = ("1.6", "1.2") if expected_unit == "percent" else ("1.1", "0.7")
        research.values[key] = list(zip((first, last), values))

    body = post(research, question, key, event).json()
    assert body["status"] == "answered" and body["support"] == "partial"
    assert body["graph_steps"][0] == "interpret_preset"
    assert [schema for schema, _ in research.calls] == [Draft]
    assert research.reads == [(key, start.isoformat(), end.isoformat())]
    assert body["facts"][0]["citations"] == [f"series:{key}"]
    assert body["observed_changes"][0]["unit"] == expected_unit
    assert body["observed_changes"][0]["value"] == expected_value
    assert ("-25%" if expected_unit == "percent" else "percentage points") in body["calculations"][0]["text"]
    assert any("causal effect" in note for note in body["limitations"])
    if key == "deposit-rate":
        assert body["observed_changes"][0]["first_observed_on"] == start.isoformat()
        assert body["observed_changes"][0]["last_observed_on"] == end.isoformat()
        assert "effective 2019-09-18" in body["facts"][0]["text"]
    if question == COVID_HICP:
        assert body["publication_cutoff"] == "2020-12-31"
        assert body["corpus_sha256"] == v2_retrieval.APPROVED_SNAPSHOT
        documents = [item for item in body["evidence"] if item["kind"] == "document"]
        assert {item["claim_id"] for item in documents} == COVID_HICP_CLAIMS
        assert all("2020-02-01" <= item["period_start"] <= item["period_end"] <= item["published_on"]
                   <= body["publication_cutoff"] for item in documents)


@pytest.mark.parametrize("question,key,event", [
    (COVID_HICP, "real-gdp-qoq", "covid"),
    (COVID_HICP, "hicp-yoy", "ukraine"),
])
def test_preset_conflicting_filters_cannot_reach_tools(research, question, key, event):
    body = post(research, question, key, event).json()
    assert body["status"] == "insufficient_evidence"
    assert not body["evidence"] and not research.reads and not research.calls


def test_deposit_rate_does_not_carry_anchor_beyond_verified_coverage(research):
    anchor_date = date(2019, 9, 18)
    research.coverage = {
        "coverage_start": date(2020, 2, 1), "coverage_end": date(2020, 12, 30),
        "anchor": data.MacroObservation(period_raw=anchor_date.isoformat(), period_start=anchor_date,
                                        period_end=anchor_date, effective_date=anchor_date,
                                        value=Decimal("-0.50"), status_kind="normal", status_raw=None),
    }
    research.values["deposit-rate"] = []
    body = post(research, "How did the COVID-19 pandemic affect ECB rate?", "deposit-rate", "covid").json()
    assert body["facts"] == body["calculations"] == body["observed_changes"] == []
    assert all(item["kind"] == "document" for item in body["evidence"])


@pytest.mark.parametrize("qualifier,override", [
    (" in Germany", {"geography": "Germany"}),
    (" measured by the HICP index", {"measure": "hicp_index"}),
    (" in USD", {"unit": "USD"}),
])
def test_modified_preset_keeps_explicit_constraints(research, qualifier, override):
    question = f"{COVID_HICP[:-1]}{qualifier}?"
    research.intent = {"purpose": "causal", "requirements": [
        {**requirement(question, "hicp-yoy", "trend"), **override},
    ]}
    body = post(research, question, "hicp-yoy", "covid").json()
    assert body["status"] == "insufficient_evidence"
    assert [schema for schema, _ in research.calls] == [Intent]
    assert not body["evidence"] and not research.reads


@pytest.mark.parametrize("override", [
    {"indicator": "real-gdp-qoq"}, {"geography": "Germany"}, {"unit": "percent"},
    {"variant": "constant_taxes"}, {"adjustment": "seasonal_only"}, {"population": "adults"},
    {"subgroup": "exporters"}, {"measure": "hicp_index"}, {"other_constraints": ["flash estimate"]},
    {"excerpt": "model-generated text"},
    {"operation": "relative_change", "unit": "percent"}, {"operands": []},
    {"operands": [window("1900-09-01", "1900-09-30"), window("2022-10-01", "2022-10-31")]},
    {"operands": [window("2022-09-02", "2022-09-30"), window("2022-10-01", "2022-10-31")]},
])
def test_unsupported_scope_cannot_reach_tools(research, override):
    item = requirement(DIFFERENCE, "hicp-yoy", "difference")
    item["operands"] = [window("2022-09-01", "2022-09-30"), window("2022-10-01", "2022-10-31")]
    research.intent = {"purpose": "descriptive", "requirements": [{**item, **override}]}
    body = post(research, DIFFERENCE, "hicp-yoy", "ukraine").json()
    assert body["status"] == "insufficient_evidence"
    assert body["evidence"] == research.reads == []
    assert [schema for schema, _ in research.calls] == [Intent]


def test_mixed_requirements_and_unknown_parts_remain_partial(research):
    question = "What happened to euro-area GDP during COVID, including raw GDP and the effect on exporters?"
    good = requirement("What happened to euro-area GDP during COVID", "real-gdp-qoq", "trend")
    raw = {**good, "excerpt": "raw GDP", "adjustment": "unadjusted"}
    research.intent = {"purpose": "descriptive", "event": "covid", "requirements": [good, raw],
                       "unresolved": ["the effect on exporters"]}
    body = post(research, question, "real-gdp-qoq", "covid").json()
    assert body["support"] == "partial"
    assert len(body["facts"]) == len(research.reads) == 1
    assert any("Part of the question" in note for note in body["limitations"])


@pytest.mark.parametrize("attack", ["new_number", "wrong_unit", "borrowed_claim", "unknown_id", "missing", "model_failure"])
def test_bad_draft_returns_only_server_statements(research, attack):
    research.intent = {"purpose": "causal", "event": "ukraine",
                       "requirements": [requirement(UKRAINE, "hicp-yoy", "trend")]}

    def draft(payload):
        if attack == "model_failure":
            raise TimeoutError
        rows = [{"evidence_id": row["evidence_id"], "text": row["sentences"][0]} for row in payload]
        if attack == "unknown_id":
            rows[0]["evidence_id"] = 99
        elif attack == "missing":
            rows.pop()
        else:
            rows[0]["text"] = {
                "new_number": "The war caused 99% inflation.",
                "wrong_unit": rows[1]["text"].replace("percentage points", "percent"),
                "borrowed_claim": rows[-1]["text"],
            }[attack]
        return {"sentences": rows}

    research.draft = draft
    body = post(research, UKRAINE, "hicp-yoy", "ukraine").json()
    assert body["narrative_kind"] == "deterministic" and body["model_id"] is None
    assert [{key: row[key] for key in ("text", "citations")} for row in body["narrative"]] == (
        body["facts"] + body["calculations"] + body["interpretation"]
    )


def test_missing_comparison_operand_cannot_use_adjacent_observation(research):
    research.values["hicp-yoy"].remove(("2022-09", "9.9"))
    item = requirement(DIFFERENCE, "hicp-yoy", "difference")
    item["operands"] = [window("2022-09-01", "2022-09-30"), window("2022-10-01", "2022-10-31")]
    research.intent = {"purpose": "descriptive", "requirements": [item]}
    body = post(research, DIFFERENCE, "hicp-yoy", "ukraine").json()
    assert body["support"] == "insufficient"
    assert body["evidence"] == body["calculations"] == []
    assert [schema for schema, _ in research.calls] == [Intent]


def test_failures_and_sensitive_input(research, monkeypatch):
    failed = post(research, FX, "eur-usd", "covid").json()
    assert failed["graph_steps"] == ["interpret_with_llm", "finish"]
    assert failed["status"] == "insufficient_evidence"
    assert post(research, "My api key is sensitive. password=example", "eur-usd", "covid").json()["evidence"] == []
    assert len(research.calls) == 1 and not research.reads

    research.intent = {"purpose": "causal", "requirements": [requirement(UKRAINE, "hicp-yoy", "trend")]}
    research.unavailable = True
    failed = post(research, UKRAINE, "hicp-yoy", "ukraine").json()
    assert failed["support"] == "partial" and all(item["kind"] == "document" for item in failed["evidence"])
    assert any("reader was unavailable" in note for note in failed["limitations"])

    research.unavailable = False
    monkeypatch.setenv("MARKETPULSE_RESEARCH_V2_RETRIEVER", "e5")
    monkeypatch.delenv("MARKETPULSE_RESEARCH_V2_E5_CACHE", raising=False)
    failed = post(research, UKRAINE, "hicp-yoy", "ukraine").json()
    assert failed["support"] == "partial" and [item["kind"] for item in failed["evidence"]] == ["series"]
    assert any("retrieval was unavailable" in note for note in failed["limitations"])


def test_flag_legacy_and_lan_boundary(research, monkeypatch):
    assert research.client.get("/macro/indicators").headers["X-MarketPulse-Research-V2"] == "true"
    monkeypatch.setattr(api, "_authorized_lan_origin", lambda *_: True)
    assert post(research, FX, "eur-usd", "covid", headers={
        "Origin": "http://192.0.2.1:5173", "X-MarketPulse-Lan-Auth": "fixture",
    }).status_code == 404
    assert not research.calls
    monkeypatch.delenv("MARKETPULSE_RESEARCH_V2_ENABLED")
    assert "X-MarketPulse-Research-V2" not in research.client.get("/macro/indicators").headers
    assert post(research, FX, "eur-usd", "covid").status_code == 404
    legacy = ResearchResponse(status="insufficient_evidence", question=FX, start="2020-02-01", end="2020-12-31",
                              limitations=["fixture"], evidence=[], graph_steps=["legacy"])
    monkeypatch.setattr(service, "workflow", lambda: SimpleNamespace(run=lambda _: legacy))
    assert post(research, FX, "eur-usd", "covid", v2=False).json() == legacy.model_dump(mode="json")
    assert not research.calls


def test_langchain_structured_schema_and_provider_budget(research, monkeypatch):
    monkeypatch.setattr(v2_gemini, "invoke_structured", gemini.invoke_structured)
    provider_calls = []

    def generate_content(_self, *, model, contents, config):
        provider_calls.append(config.response_json_schema)
        assert model == "gemini-3.6-flash" and not config.tools
        assert config.http_options.timeout == 18_000 and config.http_options.retry_options.attempts == 1
        assert config.response_mime_type == "application/json"
        prompt = contents[-1].parts[0].text
        if len(provider_calls) == 1:
            assert FX in prompt and "synthetic-key" not in prompt
            output = {"event": "covid", "purpose": "descriptive",
                      "requirements": [requirement(FX, "eur-usd", "trend")]}
        else:
            assert FX not in prompt
            rows = json.loads(prompt.partition("\n")[2])
            output = {"sentences": [{"evidence_id": row["evidence_id"], "text": row["sentences"][0]} for row in rows]}
        return GenerateContentResponse(candidates=[
            Candidate(content=Content(role="model", parts=[Part(text=json.dumps(output))])),
        ])

    monkeypatch.setattr(Models, "generate_content", generate_content)
    body = post(research, FX, "eur-usd", "covid").json()
    assert body["narrative_kind"] == "gemini"
    assert len(provider_calls) == 2
    assert set(provider_calls[0]["properties"]) == {"event", "purpose", "requirements", "unresolved"}
    assert set(provider_calls[1]["properties"]) == {"sentences"}
