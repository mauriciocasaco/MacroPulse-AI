from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

pytest.importorskip("langgraph")

from langchain_core.runnables import RunnableLambda

from backend.api import app
from backend.macro_ai import api as research_api
from backend.macro_ai import gemini, service
from backend.macro_ai.models import GeminiDraft, ResearchRequest
from backend.macro_ai.retrieval import CorpusDocument, CorpusIndex
from backend.macro_data.api import MacroHistory, MacroObservation, indicator_metadata
from backend.macro_data.contracts import CATALOG, parse_period


QUERIES = [
    ("¿Cómo evolucionó el EUR/USD durante la pandemia?", "2020-01-01", "2020-12-31", ("eur-usd",)),
    ("¿Qué ocurrió con la inflación europea después del inicio de la guerra de Ucrania?", "2022-02-24", "2023-12-31",
     ("hicp-yoy",)),
    ("¿Cómo cambiaron las tasas de interés durante 2022 y 2023?", "2022-01-01", "2023-12-31", ("deposit-rate",)),
    ("¿Qué relación observamos entre M3 y la inflación?", "2020-01-01", "2023-12-31", ("m3-stock", "hicp-yoy")),
    ("¿Cómo evolucionó el PIB de la eurozona después del COVID-19?", "2020-01-01", "2023-12-31", ("real-gdp-qoq",)),
]
CORPUS = Path("backend/macro_ai/corpus.json")


def _request(question, start, end):
    return ResearchRequest(question=question, start=date.fromisoformat(start), end=date.fromisoformat(end))


def _observation(key, raw, value, status="normal"):
    period = parse_period(CATALOG[key].frequency, raw)
    return MacroObservation(
        period_raw=raw, period_start=period.start, period_end=period.end, effective_date=period.effective_date,
        value=Decimal(value) if value is not None else None, status_kind=status, status_raw=None,
    )


def _histories(keys, request):
    out = {}
    for key in keys:
        item = CATALOG[key]
        if key == "eur-usd":
            rows = [("2020-02-01", "1.20"), ("2020-03-02", "1.25"), ("2020-11-03", "1.10")]
        elif key == "real-gdp-qoq":
            rows = [("2020-Q1", "-5.1"), ("2020-Q2", "-10.2"), ("2020-Q3", "11.0")]
        elif key == "deposit-rate":
            rows = [("2022-01-01", "1.0"), ("2023-01-01", "2.0"), ("2023-03-01", "3.0")]
        else:
            rows = [
                (f"{2020 + index // 12:04}-{index % 12 + 1:02}",
                 str(1000 + index ** 2) if key == "m3-stock" else str(Decimal(index) / 10))
                for index in range(48)
            ]
        out[key] = MacroHistory(
            indicator=indicator_metadata(item), start=request.start, end=request.end,
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            observations=[_observation(key, period, value) for period, value in rows],
            anchor=_observation(key, "2021-01-01", "0.0") if key == "deposit-rate" else None,
            coverage_start=request.start if key == "deposit-rate" else None,
            coverage_end=request.end if key == "deposit-rate" else None,
        )
    return out


def _draft(prompt, evidence):
    context = json.loads(evidence)
    return {
        "interpretation": [{
            "text": "Los movimientos observados aportan contexto para comparar la evolución durante el período.",
            "citations": [context["statistics"][0]["citations"][0], context["documents"][0]["id"]],
        }],
    }


@pytest.mark.parametrize("question,start,end,keys", QUERIES)
def test_five_demonstrations_use_statistics_retrieval_and_generation(question, start, end, keys):
    requested = []

    def read(selection, request):
        requested.append(selection)
        return _histories(selection, request)

    workflow = service.Workflow(CorpusIndex.load(CORPUS), read=read, generate=_draft)
    response = workflow.run(_request(question, start, end))
    assert requested == [keys]
    assert response.status == "answered"
    assert response.graph_steps == [
        "interpret", "select_indicators", "read_statistics", "retrieve_documents", "assess_evidence", "generate", "finish",
    ]
    assert {source.id for source in response.evidence if source.kind == "series"} == {f"series:{key}" for key in keys}
    assert all(source.published_on <= date.fromisoformat(end) for source in response.evidence if source.published_on)
    allowed = {item.id for item in response.evidence}
    assert all(set(claim.citations) <= allowed for claim in response.facts + response.calculations + response.interpretation)
    if keys == ("m3-stock", "hicp-yoy"):
        assert any("36 pares coincidentes" in claim.text for claim in response.calculations)
    if keys == ("deposit-rate",):
        assert "2021-01-01" not in response.facts[0].text
        assert response.calculations[0].text.endswith("200.0 puntos básicos.")


def test_retrieval_filters_publication_date_and_chunks_reviewed_summaries():
    item = CorpusDocument(
        id="fixture-rates", title="Institutional synthetic monetary evidence",
        url="https://www.ecb.europa.eu/fixture",
        rights_url="https://www.ecb.europa.eu/fixture-rights",
        published_on=date(2024, 2, 1), attribution="Synthetic institutional fixture",
        topics=["rates"], text=("depósito tasa rates cambios monetarios " * 50).strip(),
        excerpt="Raw excerpt placeholder should remain outside prompts.",
    )
    index = CorpusIndex([item])
    assert index.retrieve("tasa", ("rates",), date(2024, 1, 31)) == []
    matches = index.retrieve("tasa", ("rates",), date(2024, 2, 1))
    assert len(matches) == 3
    assert all(chunk.id.startswith("doc:fixture-rates:") and "excerpt placeholder" not in chunk.page_content
               for chunk in matches)
    with pytest.raises(ValueError):
        CorpusDocument.model_validate(item.model_dump() | {"url": "https://www.ecb.europa.eu.example.test/page"})


def test_graph_abstains_without_calls_and_never_sends_original_question():
    index = CorpusIndex.load(CORPUS)
    calls = []

    def generate(question, context):
        calls.append((question, context))
        return _draft(question, context)

    workflow = service.Workflow(index, read=_histories, generate=generate)
    unsupported = workflow.run(_request("¿Qué receta de cocina recomiendas?", "2020-01-01", "2020-12-31"))
    assert unsupported.status == "insufficient_evidence"
    assert unsupported.graph_steps == ["interpret", "select_indicators", "abstain"]
    no_sources = workflow.run(_request("¿Cómo evolucionó el EUR/USD durante la pandemia?", "2018-01-01", "2018-12-31"))
    assert no_sources.status == "insufficient_evidence" and no_sources.graph_steps[-1] == "abstain"
    assert calls == []
    answered = workflow.run(_request(
        "¿Cómo evolucionó EUR/USD durante la pandemia? fixture marcador privado", "2020-01-01", "2020-12-31",
    ))
    assert answered.status == "answered" and len(calls) == 1
    assert all("marcador privado" not in message for message in calls[0])


@pytest.mark.parametrize("change", ["invented-citation", "duplicate-citation", "only-series", "generation-failure"])
def test_generation_failures_end_in_fixed_error_state(change):
    def invalid(question, evidence):
        draft = _draft(question, evidence)
        claim = draft["interpretation"][0]
        if change == "invented-citation":
            claim["citations"][1] = "doc:invented"
        elif change == "duplicate-citation":
            claim["citations"].append(claim["citations"][1])
        elif change == "only-series":
            claim["citations"].pop()
        else:
            raise RuntimeError("synthetic private model details")
        return draft

    workflow = service.Workflow(CorpusIndex.load(CORPUS), read=_histories, generate=invalid)
    with pytest.raises(service.WorkflowFailure, match="macro research unavailable") as error:
        workflow.run(_request(*QUERIES[0][:3]))
    assert error.value.steps[-2:] == ["generate", "handle_error"]


def test_model_selects_evidence_but_its_false_peak_chronology_is_never_published():
    index = CorpusIndex.load(CORPUS)
    wrong = "M3 alcanzó su máximo meses antes del pico del IAPC y provocó el cambio de 18 unidades."

    def read(keys, request):
        histories = _histories(keys, request)
        for key, values in {"m3-stock": ("150", "170", "160"), "hicp-yoy": ("8", "9", "10")}.items():
            histories[key].observations = [
                _observation(key, period, value)
                for period, value in zip(("2022-08", "2022-09", "2022-10"), values, strict=True)
            ]
        return histories

    def answer(index_in_retrieval):
        chosen = []

        def model(_question, evidence):
            document = json.loads(evidence)["documents"][index_in_retrieval]
            chosen.append(document["id"])
            return {"interpretation": [{
                "text": wrong, "citations": ["series:m3-stock", "series:hicp-yoy", document["id"]],
            }]}

        result = service.Workflow(index, read=read, generate=model).run(_request(*QUERIES[3][:3]))
        assert result.status == "answered"
        assert "170 en 2022-09" in result.facts[0].text and "10 en 2022-10" in result.facts[1].text
        assert all(phrase not in result.model_dump_json() for phrase in ("meses antes", "provocó", "18 unidades"))
        rendered = result.interpretation[0]
        assert rendered.citations == ["series:m3-stock", "series:hicp-yoy", chosen[0]]
        summary = next(chunk.page_content for chunk in index.chunks if chunk.id == chosen[0])
        assert summary.split(". ", 1)[0] in rendered.text
        return rendered.text

    assert answer(0) != answer(1)


def test_provider_uses_real_prompt_chain_with_structured_schema_and_one_attempt(monkeypatch):
    seen = {}

    class Client:
        def close(self):
            seen["closed"] = True

    class Chat:
        client = Client()

        def __init__(self, **options):
            seen["options"] = options

        def with_structured_output(self, schema, *, method):
            seen["schema"] = schema
            seen["method"] = method

            def invoke(prompt, *, http_options):
                seen["messages"] = prompt.to_messages()
                seen["http"] = http_options
                return GeminiDraft.model_validate(_draft("", json.dumps({
                    "statistics": [{"citations": ["series:eur-usd"]}],
                    "documents": [{"id": "doc:fixture:1"}],
                })))

            return RunnableLambda(invoke)

    monkeypatch.setenv("GEMINI_API_KEY", sha256(b"synthetic API key fixture").hexdigest())
    monkeypatch.delenv(gemini.MODEL_ENV, raising=False)
    monkeypatch.setattr(gemini, "ChatGoogleGenerativeAI", Chat)
    output = gemini.invoke_gemini("Intención canónica", "Contexto institucional sintetizado")
    assert isinstance(output, GeminiDraft) and seen["closed"]
    assert seen["schema"] is GeminiDraft and seen["method"] == "json_schema"
    assert seen["options"]["max_retries"] == 0 and seen["options"]["vertexai"] is False
    assert seen["http"].retry_options.attempts == 1 and seen["http"].timeout == 18_000
    assert "Contexto institucional sintetizado" in seen["messages"][-1].content


def test_local_route_validates_origin_body_and_hides_internal_error(monkeypatch):
    payload = {"question": QUERIES[0][0], "start": QUERIES[0][1], "end": QUERIES[0][2]}
    with TestClient(app) as client:
        assert client.post("/macro/analysis", json=payload).status_code == 404
        monkeypatch.setattr(research_api, "_require_local", lambda _: None)
        monkeypatch.delenv("MARKETPULSE_MACRO_AI_ENABLED", raising=False)
        assert client.post("/macro/analysis", json=payload).status_code == 404
        monkeypatch.setenv("MARKETPULSE_MACRO_AI_ENABLED", "true")
        assert client.post("/macro/analysis", json=payload, headers={"Origin": "https://example.test"}).status_code == 403
        assert client.post("/macro/analysis", content=b"fixture").status_code == 415
        assert client.post("/macro/analysis", json=payload | {"unknown": "fixture"}).status_code == 422
        assert client.post("/macro/analysis", json=payload | {"question": "z" * 5000}).status_code == 413
        workflow = service.Workflow(CorpusIndex.load(CORPUS), read=_histories, generate=_draft)
        monkeypatch.setattr(service, "workflow", lambda: workflow)
        response = client.post("/macro/analysis", json=payload, headers={"Origin": "http://127.0.0.1:5173"})
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"

        def fail():
            raise RuntimeError("synthetic private DB or provider error details")

        monkeypatch.setattr(service, "workflow", fail)
        response = client.post("/macro/analysis", json=payload)
        assert response.status_code == 503 and response.json() == {"detail": "Macro research unavailable"}


def test_public_demo_import_avoids_optional_packages_and_analysis_route(tmp_path):
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("Synthetic CA path. Public app is imported without starting lifespan.")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PG", "MARKETPULSE_", "MARKET_ANALYSIS_")) and key not in {
               "MARKET_API_DSN", "MACRO_API_DSN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "DATABASE_URL",
               "USER_RO", "PASS_USER_RO", "HOST_RO", "PORT_RO", "DB_RO", "SSL_MODE_RO", "SSL_ROOT_CERT_RO",
           }}
    synthetic_password = sha256(b"synthetic public startup password fixture").hexdigest()
    env.update(
        MARKETPULSE_MODE="public_demo",
        MARKETPULSE_DEMO_PROJECT_REF="abcdefghijklmnopqrst",
        MARKETPULSE_DEMO_DSN=("host=db.abcdefghijklmnopqrst.supabase.co port=5432 dbname=postgres "
                              f"user=marketpulse_demo_ro password={synthetic_password}"),
        MARKETPULSE_DEMO_ORIGIN_SECRET=sha256(b"synthetic public origin fixture").hexdigest(),
        MARKETPULSE_DEMO_SSL_ROOT_CERT=str(ca),
    )
    subprocess.run([sys.executable, "-c", (
        "import sys; from backend.api import app; "
        "assert not any(m.startswith(('backend.macro_ai', 'langchain_core', 'langgraph', 'google.genai')) for m in sys.modules); "
        "assert '/macro/analysis' not in {route.path for route in app.routes}"
    )], env=env, check=True, timeout=15)
