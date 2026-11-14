"""All event and KPI combinations with injected series and editorial fixtures."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from backend.macro_ai.models import Claim, ResearchRequest
from backend.macro_ai.v2_models import CuratedEvidence, Intent, Requirement
from backend.macro_ai.v2_service import ResearchRuntime, run
from backend.macro_ai.v2_statistics import DEFINITIONS
from backend.macro_data.api import MacroHistory, MacroObservation, indicator_metadata
from backend.macro_data.contracts import CATALOG, parse_period
from scripts.evaluate_dynamic_rag import EXPECTED_EDITORIAL, report, scenarios, simulated_compose


def _history(key, window):
    item = CATALOG[key]
    year = window.start.year
    periods = {
        "daily": [window.start.isoformat(), window.end.isoformat()],
        "monthly": [f"{year}-{window.start.month:02}", f"{year}-12"],
        "quarterly": [f"{year + (window.start.month > 4)}-Q2", f"{year + (window.start.month > 4)}-Q3"],
        "effective_date": [window.start.isoformat(), window.end.isoformat()],
    }
    observations = []
    for index, raw in enumerate(periods[item.frequency]):
        period = parse_period(item.frequency, raw)
        observations.append(MacroObservation(
            period_raw=raw, period_start=period.start, period_end=period.end,
            effective_date=period.effective_date, value=Decimal("1.1") + index,
            status_kind="normal", status_raw=None,
        ))
    return MacroHistory(
        indicator=indicator_metadata(item), start=window.start, end=window.end, updated_at=None,
        observations=observations, coverage_start=window.start, coverage_end=window.end,
    )


def editorial_fixture(_question, event, key, window, cutoff):
    available = datetime(2026, 11, 14, tzinfo=timezone.utc)
    if key not in EXPECTED_EDITORIAL[event] or available.date() > cutoff:
        return []
    proxy = event == "generative-ai"
    source = CuratedEvidence(
        id="claim:fixture:" + "a" * 64, kind="document", title="Approved fixture",
        url="https://example.org/report#table", attribution="Fixture institution",
        claim_id="fixture", claim_version="a" * 64, document_id="0" * 64,
        event="genai" if proxy else event, indicator=key, geography=DEFINITIONS[key].geography[0],
        locator="Table 1", qualifiers="No causal estimate.", claim_type="proxy" if proxy else "observed",
        relation="proxy" if proxy else "direct", published_on=window.end, available_at=available,
        period_start=window.start, period_end=window.end, source_id="fixture",
        measure="adoption" if proxy else DEFINITIONS[key].measure, unit=CATALOG[key].unit,
        event_linkage="contemporaneous_non_causal",
    )
    return [(source, Claim(text="Approved synthetic statement.", citations=[source.id]))]


@pytest.mark.parametrize("request_data", list(scenarios()), ids=lambda row: f"{row.event_id}-{row.indicator_id}")
def test_dynamic_research_matrix(request_data):
    runtime = ResearchRuntime(
        _history, lambda _: pytest.fail("Preset must resolve deterministically"), simulated_compose, "simulated",
        supplemental_retriever=editorial_fixture, dynamic_editorial=True, knowledge_cutoff=date(2026, 11, 14),
    )
    result = run(request_data, runtime=runtime)
    entry = report(request_data, result, 0)
    expected = request_data.indicator_id in EXPECTED_EDITORIAL[request_data.event_id]
    assert entry["outcome"] == "PASS", result
    assert bool(entry["editorial"]) == expected
    assert bool(entry["fallback"]) == (request_data.event_id == "covid" and request_data.indicator_id == "hicp-yoy")
    assert result.publication_cutoff >= request_data.end
    if request_data.event_id == "generative-ai" and expected:
        assert any(row.category == "proxy context" for row in result.narrative)


def test_unsupported_free_question_abstains():
    request = ResearchRequest(question="What was the monthly euro-area inflation rate during COVID?",
                              start=date(2020, 2, 1), end=date(2020, 12, 31))
    intent = Intent(event="covid", purpose="descriptive", requirements=[Requirement(
        excerpt=request.question, indicator="hicp-yoy", operation="trend",
        geography="euro_area", measure="monthly_inflation",
    )])

    def unexpected(*_):
        pytest.fail("Unsupported measure must abstain before using a tool")

    result = run(request, runtime=ResearchRuntime(
        unexpected, lambda _: intent, unexpected, "simulated",
        supplemental_retriever=unexpected, dynamic_editorial=True,
    ))
    assert result.status == "insufficient_evidence" and not result.evidence
