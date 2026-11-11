"""Experimental gate regressions using public approved data and synthetic inputs."""
from pathlib import Path
import shutil

from pydantic import ValidationError
import pytest

from backend.m7c.corpus import Scope
from backend.m7c.retrieval import Ranked
from backend.m7d import EvidenceGate, Intent, OfficialMovement, Requirement, Times
from backend.m7d.evaluate import evaluate
from backend.macro_data.contracts import CATALOG

ROOT = Path(__file__).resolve().parents[1]
M7B = ROOT / "docs/m7b"


@pytest.fixture(scope="module")
def gate():
    return EvidenceGate(M7B)


def scope(**changes):
    return Scope(**{
        "event": "covid", "indicator": "hicp-yoy", "cutoff": "2020-12-31", "geography": "euro-area", **changes,
    })


def test_one_document_can_ground_two_requested_facts(gate):
    answer = gate.assess("What was headline annual inflation in November 2020, and what did energy contribute?", scope())
    assert answer.status == "sufficient"
    first, second = answer.grounding_claims
    assert first.document_id == second.document_id
    assert first.claim_id != second.claim_id
    assert first.covered_measures == ("hicp_annual",)
    assert second.covered_measures == ("hicp_energy",)
    assert first.reference_start == "2020-11-01"
    assert first.publication_date == "2020-12-17"
    assert answer.scope.cutoff == "2020-12-31"
    assert second.qualifiers and second.locator and second.source_url.startswith("https://")
    with pytest.raises(ValidationError):
        first.use = "context_only"


@pytest.mark.parametrize("question", [
    "What was annual inflation for veterinary visits in April 2020?",
    "What was the April 2020 HICP inflation rate on 18 April 2020?",
    "How much did energy contribute to HICP in April 2020, in percent?",
    "What was month-on-month euro area inflation in April 2020?",
    "What was Spanish inflation in April 2020?",
    "What was the median annual inflation rate across euro-area cities in April 2020?",
    "What was the inflation rate in April 2020 according to the December 2020 release?",
])
def test_same_scope_is_not_enough(gate, question):
    result = gate.assess(question, scope())
    assert result.status == "insufficient"
    assert not result.selected_claims


def test_semantic_predicate_and_publication_cutoff_are_separate(gate):
    question = "In percentage points, how much did energy subtract from HICP in April 2020?"
    assert gate.assess(question, scope(cutoff="2020-05-19")).status == "insufficient"
    answer = gate.assess(question, scope(cutoff="2020-05-20"))
    assert answer.status == "sufficient"
    assert answer.selected_claims[0].covered_measures == ("hicp_energy",)


def test_unanswered_conjunct_makes_result_partial(gate):
    answer = gate.assess("What was annual HICP in April 2020, and how did ticket prices move?", scope())
    assert answer.status == "partial"
    assert len(answer.grounding_claims) == 1
    assert "incomplete_predicate_coverage" in answer.limitations
    named = gate.assess("What were annual inflation and the import price index in April 2020?", scope())
    assert named.status == "partial"
    assert len(named.grounding_claims) == 1


def test_causal_context_never_becomes_grounding(gate):
    question = "According to Eurostat, how much did the pandemic cause GDP to fall in 2020 Q2?"
    result = gate.assess(question, scope(indicator="real-gdp-qoq"))
    assert result.status == "unsupported_causal"
    assert result.selected_claims
    assert not result.grounding_claims
    assert all(claim.claim_type == "observed" for claim in result.selected_claims)


def test_proxy_can_answer_its_own_measure_but_not_gdp(gate):
    ai = scope(event="genai", indicator="real-gdp-qoq", geography="eu", cutoff="2026-09-30")
    adoption = gate.assess("What share of EU enterprises used AI in 2024?", ai)
    assert adoption.status == "sufficient"
    assert adoption.grounding_claims[0].relation == "proxy"
    assert adoption.grounding_claims[0].covered_measures == ("ai_enterprise",)
    result = gate.assess("How much GDP growth did generative AI cause in the EU in 2024?", ai)
    assert result.status == "unsupported_causal"
    assert result.selected_claims and not result.grounding_claims
    assert gate.assess("What GDP growth did the EU record in 2024?", ai).status == "insufficient"


@pytest.mark.parametrize("question", [
    "How many EU businesses used AI in 2023? Report the total as a whole number.",
    "¿Cuántas empresas de la UE utilizaban IA en 2023?",
    "What share of EU enterprises was expected to use AI in 2024?",
    "What proportion of EU households used generative AI in 2025?",
    "What proportion of EU residents aged 16 to 65 used generative AI in 2025?",
    "What fraction of EU enterprises with exactly ten employees used AI in 2023?",
    "What share of EU enterprises used AI for recruiting in 2023?",
])
def test_adoption_requires_matching_population_unit_technology_and_modality(gate, question):
    ai = scope(event="genai", indicator="real-gdp-qoq", geography="eu", cutoff="2025-12-31")
    answer = gate.assess(question, ai)
    assert answer.status == "insufficient"
    assert not answer.grounding_claims


def test_coordinated_technologies_require_distinct_coverage(gate):
    ai = scope(event="genai", indicator="real-gdp-qoq", geography="eu", cutoff="2025-12-31")
    partial = gate.assess("What fraction of EU firms used automated workflows and speech recognition in 2023?", ai)
    assert partial.status == "partial"
    assert [claim.covered_measures for claim in partial.grounding_claims] == [("ai_automation",)]
    joint = gate.assess("What share of EU firms used AI and automated workflows in 2023?", ai)
    assert joint.status == "partial"
    assert {claim.covered_measures for claim in joint.grounding_claims} == {("ai_enterprise",), ("ai_automation",)}


@pytest.mark.parametrize("question", [
    "How much did energy and utilities contribute to HICP in April 2020?",
    "How much did energy plus health contribute to HICP in April 2020?",
    "What was the contribution of transport, energy, and utilities to HICP in April 2020?",
    "¿Cuánto aportaron la energía y los servicios a la inflación en abril de 2020?",
])
def test_component_lists_do_not_drop_unrecognized_operands(gate, question):
    result = gate.assess(question, scope())
    assert result.status == "partial"
    assert [claim.covered_measures for claim in result.grounding_claims] == [("hicp_energy",)]


def test_known_component_operands_are_separate_from_joint_and_unknown_amounts(gate):
    ukraine = scope(event="ukraine", cutoff="2023-06-01")
    individual = gate.assess("How much did energy and food contribute separately to HICP in February 2023?", ukraine)
    assert individual.status == "sufficient"
    assert set(individual.grounding_claims[0].covered_measures) == {"hicp_energy", "hicp_food"}
    joint = gate.assess("How much did energy and food contribute to HICP in February 2023?", ukraine)
    assert joint.status == "partial"
    assert gate.assess("How much did utilities contribute to HICP in April 2020?", scope()).status == "insufficient"


@pytest.mark.parametrize("question", [
    "What would annual HICP have been in April 2020 without the pandemic?",
    "What would annual HICP have been in April 2020 had lockdown never happened?",
    "Had there been no pandemic, what would annual HICP have been in April 2020?",
    "¿Cuánto habría sido el IPCA interanual de abril de 2020 si no hubiera ocurrido la pandemia?",
])
def test_counterfactuals_can_retain_observed_context_but_never_ground_it_as_an_effect(gate, question):
    answer = gate.assess(question, scope())
    assert answer.status == "unsupported_causal"
    assert answer.selected_claims
    assert all(item.use == "context_only" for item in answer.selected_claims)
    assert not answer.grounding_claims


@pytest.mark.parametrize("question", [
    "What share of EU enterprises did not use AI in 2024?",
    "What share of EU enterprises used AI in 2024 only in hiring?",
    "What share of EU enterprises used AI in hiring in 2024?",
    "What share of EU enterprises used AI in 2024 in hiring?",
    "What share of EU healthcare firms used AI in 2024?",
    "What share of all EU firms used AI in 2024?",
])
def test_polarity_and_restricted_adoption_universe_are_preserved(gate, question):
    ai = scope(event="genai", indicator="real-gdp-qoq", geography="eu", cutoff="2025-12-31")
    result = gate.assess(question, ai)
    assert result.status == "insufficient"
    assert not result.grounding_claims


@pytest.mark.parametrize("question", [
    "What was annual inflation before the April 2020 lockdown?",
    "What was annual inflation after April 2020?",
    "What was annual inflation after the start of April 2020?",
    "What was annual inflation no later than April 2020?",
    "What was annual inflation in early April 2020?",
    "What was annual inflation in mid-April 2020?",
    "What was the seasonally adjusted annual HICP in April 2020?",
    "What was the calendar-corrected annual HICP in April 2020?",
])
def test_temporal_operators_and_adjustment_qualifiers_cannot_be_dropped(gate, question):
    result = gate.assess(question, scope())
    assert result.status == "insufficient"
    assert not result.grounding_claims


@pytest.mark.parametrize("question", [
    "How much did the pandemic drive annual inflation in April 2020?",
    "How many percentage points of HICP inflation did the COVID-19 pandemic cause in April 2020?",
    "¿Cuánto aumentó la inflación por la pandemia en abril de 2020?",
])
def test_causal_request_keeps_observed_context_without_transferring_units(gate, question):
    answer = gate.assess(question, scope())
    assert answer.status == "unsupported_causal"
    assert len(answer.selected_claims) == 1
    assert not answer.grounding_claims


@pytest.mark.parametrize("question", [
    "What was Slovenian real GDP growth in the fourth quarter of 2023?",
    "What was real GDP growth for Lithuania in the fourth quarter of 2023?",
    "¿Cuál fue el crecimiento del PIB real esloveno en el cuarto trimestre de 2023?",
    "What was EA21 real GDP growth in Q4 2023?",
])
def test_gdp_scope_preserves_geographic_subject_modifiers(gate, question):
    euro_area = scope(event="ukraine", indicator="real-gdp-qoq", cutoff="2024-02-29")
    assert gate.assess(question, euro_area).status == "insufficient"


def test_explicit_event_cannot_be_replaced_by_scope(gate):
    assert gate.assess("What was annual HICP in April 2020 during the war in Ukraine?", scope()).status == "insufficient"


def test_coordinated_periods_keep_publication_cutoff_and_coverage_separate(gate):
    question = "What was annual HICP in April and November 2020?"
    complete = gate.assess(question, scope())
    assert complete.status == "sufficient"
    assert [claim.reference_start for claim in complete.grounding_claims] == ["2020-04-01", "2020-11-01"]
    partial = gate.assess(question, scope(cutoff="2020-12-16"))
    assert partial.status == "partial"
    assert [claim.reference_start for claim in partial.grounding_claims] == ["2020-04-01"]
    assert gate.assess("What was annual HICP in April to November 2020?", scope()).status == "insufficient"
    vintage = gate.assess("In the December 2020 release, what was annual HICP in April and November 2020?", scope())
    assert vintage.status == "partial"
    assert [claim.reference_start for claim in vintage.grounding_claims] == ["2020-11-01"]


def test_expectation_and_implemented_rate_have_separate_predicates(gate):
    policy = scope(event="ukraine", indicator="deposit-rate", cutoff="2022-09-10")
    intended = gate.assess("What rate increase did the ECB intend in June for the July meeting?", policy)
    assert intended.status == "sufficient"
    assert intended.grounding_claims[0].claim_type == "projection_or_expectation"
    assert gate.assess("What deposit rate did the ECB set in July 2022?", policy).status == "insufficient"
    assert gate.assess("When did the 0.80% deposit rate announced in September 2022 take effect?", policy).status == "insufficient"


def movement(**changes):
    series = CATALOG["eur-usd"]
    return OfficialMovement(**{
        "indicator": "eur-usd", "event": "covid", "geography": "euro-area", "value": "-1.20",
        "unit": "percent", "direction": "down", "first_observed_on": "2020-03-02",
        "last_observed_on": "2020-03-31", "available_on": "2020-04-01", "citation": "series:eur-usd",
        "source_url": series.source_url, "attribution": series.attribution, **changes,
    })


def test_series_supplies_observation_and_preserves_causal_abstention(gate):
    fx = scope(indicator="eur-usd")
    observation = movement()
    observed = gate.assess("How did spot EUR/USD move in March 2020?", fx, movement=observation)
    assert observed.status == "sufficient"
    assert observed.official_movement == observation
    assert not observed.selected_claims
    cause = gate.assess("How did the pandemic move spot EUR/USD in March 2020?", fx, movement=observation)
    assert cause.status == "unsupported_causal" and cause.official_movement == observation
    assert not cause.grounding_claims
    for question in (
        "How did official series liquidity in EUR/USD swaps move in March 2020?",
        "How did spot EUR/USD move in February 2020?",
    ):
        assert gate.assess(question, fx, movement=observation).status == "insufficient"


def test_series_vintage_and_structured_scope(gate):
    fx = scope(indicator="eur-usd", cutoff="2020-03-31")
    result = gate.assess("How did spot EUR/USD move in March 2020?", fx, movement=movement())
    assert result.status == "insufficient" and result.official_movement is None
    assert "official_vintage_unverified_or_out_of_scope" in result.limitations
    incompatible = Intent((Requirement("gdp_quarterly", Times()),), causal=False)
    result = gate.assess_intent("What did GDP do?", fx, incompatible)
    assert result.status == "insufficient"
    with pytest.raises(ValidationError, match="official catalog"):
        movement(source_url="https://example.invalid/rates")


def test_unapproved_claim_and_ranker_injection_fail_closed(gate, tmp_path):
    for name in ("curated-documents.jsonl", "evidence-claims.jsonl", "review-decisions.jsonl", "editorial-review.json"):
        shutil.copyfile(M7B / name, tmp_path / name)
    file = tmp_path / "evidence-claims.jsonl"
    file.write_bytes(file.read_bytes().replace(b'"text": "', b'"text": "Synthetic unreviewed assertion. ', 1))
    with pytest.raises(ValueError, match="editorial review"):
        EvidenceGate(tmp_path)

    class InvalidRanker:
        def rank(self, question, candidates):
            return [Ranked("a" * 64, 1.0)]

    invalid = EvidenceGate(M7B, ranker=lambda _: InvalidRanker())
    with pytest.raises(ValueError, match="eligible claim IDs"):
        invalid.assess("What was annual inflation in April 2020?", scope())


def test_frozen_m7c_benchmark_meets_claim_recall_and_intent_abstention():
    report = evaluate(M7B, ROOT / "docs/m7c/evaluation.jsonl", "regression", model=None, repeats=1)
    summary = report["metrics"]["gate_lexical"]
    assert summary["cases"] == 28
    assert summary["claim_hit_at_3"] >= 15 / 17
    assert summary["false_answers"] == 0
    assert summary["cases_without_relevant_claims_with_hits"] == 0
    assert summary["multi_claim_cases_with_all_groups"] == 1
