from datetime import date

import pytest
from psycopg import OperationalError

from backend.macro_ai.models import EVENT_WINDOWS, ResearchRequest
from backend.macro_ai.v2_service import ResearchRuntime, run
from backend.public_online import online_knowledge_cutoff


@pytest.mark.parametrize("question,expected", [
    ("What happened to EUR/USD in 2020?", None),
    ("What happened during COVID as-of 2020?", date(2020, 12, 31)),
    ("¿Qué pasó con el EUR/USD con corte al 2020-07-31?", date(2020, 7, 31)),
])
def test_explicit_knowledge_cutoff(question, expected):
    assert online_knowledge_cutoff(question) == expected


def test_dynamic_retrieval_network_failure_uses_only_verified_fallback():
    start, end = EVENT_WINDOWS["covid"]
    request = ResearchRequest(
        start=start, end=end, event_id="covid", indicator_id="eur-usd",
        question="How did the COVID-19 pandemic affect EUR/USD rate?",
    )

    def unavailable(*_):
        raise OperationalError("fixture: restricted network error details")

    def invalid_draft(_):
        return {"paragraphs": [{"statement_id": 16, "style": "direct"}]}

    result = run(request, runtime=ResearchRuntime(
        lambda *_: None, lambda *_: None, invalid_draft, "simulated",
        supplemental_retriever=unavailable, dynamic_editorial=True,
    ))
    assert result.narrative_kind == "deterministic"
    assert result.support == "partial" and result.corpus_sha256
    assert "Live editorial retrieval was unavailable." in result.limitations
    assert "restricted network" not in result.model_dump_json()
    assert {citation for row in result.narrative for citation in row.citations} == {
        item.id for item in result.evidence
    }
    assert all(item.claim_version is None and item.attribution for item in result.evidence)
