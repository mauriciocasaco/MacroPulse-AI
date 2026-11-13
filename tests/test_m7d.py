"""Experimental gate regressions using public approved data and synthetic inputs."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import shutil

from pydantic import ValidationError
import pytest

from backend.m7c.corpus import Corpus, Scope
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
    missing = gate.assess("What fraction of EU firms used automated workflows and speech recognition in 2023?", ai)
    assert missing.status == "insufficient"
    assert not missing.grounding_claims
    partial = gate.assess(
        "What fraction of EU firms used natural language generation and speech recognition in 2024, respectively?", ai,
    )
    assert partial.status == "partial"
    assert [claim.covered_measures for claim in partial.grounding_claims] == [("ai_language",)]
    joint = gate.assess("What share of EU firms used AI and natural language generation in 2024?", ai)
    assert joint.status == "insufficient"
    assert not joint.grounding_claims


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
    assert summary["claim_hit_at_3"] >= 16 / 18
    assert summary["false_answers"] == 0
    assert summary["cases_without_relevant_claims_with_hits"] == 0
    assert summary["multi_claim_cases_with_all_groups"] == 1


@pytest.mark.parametrize("question", [
    "What was the annual HICP inflation rate at constant taxes in April 2020?",
    "¿Cuál fue en abril de 2020 la tasa interanual del IPCA a impuestos constantes?",
    "Report the April 2020 annual HICP excluding changes in taxes.",
    "What was the change in the annual HICP rate in April 2020?",
    "What was the month-over-month HICP rate in April 2020?",
    "What was the quarter-over-quarter HICP rate in April 2020?",
])
def test_headline_hicp_cannot_ground_another_variant_or_statistic(gate, question):
    answer = gate.assess(question, scope())
    assert answer.status == "insufficient"
    assert not answer.grounding_claims


def test_mixed_hicp_request_authorizes_only_the_headline_requirement(gate):
    result = gate.assess(
        "What was headline HICP in April 2020 and what was HICP at constant taxes?", scope(),
    )
    assert result.status == "partial"
    headline, constant_tax = result.requirement_coverage
    assert headline.requirement.variant == "headline" and headline.status == "supported"
    assert constant_tax.requirement.variant == "constant_tax" and constant_tax.status == "uncovered"
    assert len(result.grounding_claims) == 1
    assert result.grounding_claims[0].covered_requirements == (headline.index,)
    assert not constant_tax.claim_ids


def test_mixed_hicp_frequencies_do_not_share_grounding(gate):
    result = gate.assess(
        "Give the headline annual HICP for April 2020 and the monthly HICP for April 2020.", scope(),
    )
    assert result.status == "partial"
    headline, monthly = result.requirement_coverage
    assert headline.status == "supported"
    assert monthly.status == "uncovered" and "monthly" in monthly.unmatched_text
    assert result.grounding_claims[0].covered_requirements == (headline.index,)


@pytest.mark.parametrize("question", [
    "How much did HICP increase versus the preceding month in April 2020?",
    "¿Cuánto aumentó el IPCA respecto del mes previo en abril de 2020?",
    "By how many points did the April 2020 annual HICP inflation rate exceed its March level?",
    "What was the gap in annual HICP in April 2020?",
    "What was the change in euro area HICP in April 2020?",
    "How much did HICP go up in April 2020?",
])
def test_hicp_comparison_cannot_use_a_level_as_the_difference(gate, question):
    result = gate.assess(question, scope())
    assert result.status == "insufficient"
    assert not result.grounding_claims


def test_explicit_constant_tax_evidence_covers_only_its_variant(monkeypatch):
    corpus = Corpus.load(M7B)
    claim = next(row for row in corpus.claims.values() if row.text.startswith("Euro area HICP inflation was 0.3%"))
    corpus.claims[claim.id] = claim.model_copy(update={
        "text": "Euro area HICP inflation at constant taxes was 0.2% year on year in April 2020.",
        "qualifiers": "Synthetic constant-tax observation.",
    })
    monkeypatch.setattr(Corpus, "load", lambda _: corpus)
    synthetic = EvidenceGate(M7B)
    assert synthetic.assess("What was annual HICP at constant taxes in April 2020?", scope()).status == "sufficient"
    assert synthetic.assess("What was headline HICP in April 2020?", scope()).status == "insufficient"


@pytest.mark.parametrize("question", [
    "What was the September 2022 average ECB deposit facility rate?",
    "¿A cuánto ascendió en promedio la tasa de la facilidad de depósito en septiembre de 2022?",
    "What was the maximum ECB deposit facility rate in September 2022?",
    "What nominal deposit facility rate was actually in effect on 10 September 2022?",
    "What was the ECB deposit facility rate in force during its decision on 8 September 2022?",
    "What deposit facility rate did the ECB announce on 8 September 2022 that was in force that same day?",
    "What was the ECB's deposit facility rate net of taxes at its September 2022 meeting?",
    "¿Qué tasa de depósito neta de impuestos anunció el BCE en septiembre de 2022?",
])
def test_policy_decision_cannot_ground_a_statistic_or_after_tax_rate(gate, question):
    policy = scope(event="ukraine", indicator="deposit-rate", cutoff="2022-09-10")
    result = gate.assess(question, policy)
    assert result.status == "insufficient"
    assert not result.grounding_claims


def test_policy_announcement_publication_and_effective_date_are_distinct(gate):
    policy = scope(event="ukraine", indicator="deposit-rate", cutoff="2022-09-08")
    question = "What deposit facility rate did the ECB announce on 8 September 2022, effective 14 September 2022?"
    assert gate.assess(question, policy).status == "sufficient"
    assert gate.assess(question, scope(event="ukraine", indicator="deposit-rate", cutoff="2022-09-07")).status == "insufficient"
    later = scope(event="ukraine", indicator="deposit-rate", cutoff="2022-09-14")
    assert gate.assess("What was the actual deposit facility rate on 14 September 2022?", later).status == "insufficient"


@pytest.mark.parametrize("question", [
    "What share of EU firms that export used AI in 2024, and what was their turnover?",
    "¿Qué proporción de las empresas exportadoras de la UE usó IA en 2024?",
    "What proportion of large EU businesses adopted AI in 2024?",
    "¿Cuántas de cada cien pymes de la UE usaron IA en 2024?",
    "For recruitment, what percentage of EU businesses used AI in 2024?",
    "For recruitment in 2024 what percentage of EU businesses used AI?",
    "What share of EU households used generative AI for work in 2025?",
    "What percentage of employed EU residents used generative AI for work in 2025?",
    "What percentage of female EU residents used generative AI for work in 2025?",
    "What percentage of retired people used generative AI for work in 2025?",
    "What percentage of EU adults used generative AI for work in 2025?",
    "¿Qué porcentaje de los adultos de la UE usó IA generativa con fines de trabajo en 2025?",
    "What share of EU firms used all AI technologies in 2024?",
    "What share of EU residents used generative AI for both work and formal education in 2025?",
    "What proportion of EU people used generative AI for work or formal education in 2025?",
    "¿Qué porcentaje de personas de la UE usó IA generativa para trabajo y educación formal en 2025?",
])
def test_adoption_subgroups_and_purposes_do_not_inherit_aggregate_grounding(gate, question):
    ai = scope(event="genai", indicator="real-gdp-qoq", geography="eu", cutoff="2025-12-31")
    result = gate.assess(question, ai)
    assert result.status == "insufficient"
    assert not result.grounding_claims
    assert all(not claim.covered_requirements for claim in result.selected_claims)


def test_separate_purposes_can_use_the_same_claim_without_grounding_joint_usage(gate):
    ai = scope(event="genai", indicator="real-gdp-qoq", geography="eu", cutoff="2025-12-31")
    result = gate.assess(
        "What shares of EU residents used generative AI for work and formal education in 2025, respectively?", ai,
    )
    assert result.status == "sufficient"
    assert {row.requirement.purpose for row in result.requirement_coverage} == {"work", "formal_education"}
    assert len(result.grounding_claims) == 1
    assert result.grounding_claims[0].covered_requirements == (0, 1)


def test_rate_average_requires_complete_published_coverage(monkeypatch):
    # Exercise the calculation with in-memory observations at the corpus boundary.
    corpus = Corpus.load(M7B)
    template = next(claim for claim in corpus.claims.values() if claim.text.startswith("On 8 September 2022"))
    document = corpus.documents[template.document_id]
    observations = [
        ("1" * 64, "2022-09-01", "2022-09-10", "2022-09-10", "0.25"),
        ("2" * 64, "2022-09-11", "2022-09-30", "2022-10-01", "1.00"),
    ]
    claims = {}
    documents = {}
    for index, (key, start, end, published, value) in enumerate(observations):
        document_id = str(index + 3) * 64
        claims[key] = template.model_copy(update={
            "id": key, "document_id": document_id,
            "text": f"The ECB deposit facility rate was {value}% from {start} through {end}.",
            "historical_period": template.historical_period.model_copy(update={"start": start, "end": end}),
            "publication_date": published,
        })
        documents[document_id] = document.model_copy(update={
            "id": document_id, "publication_date": published, "claim_ids": [key],
        })
    fixture = replace(corpus, claims=claims, documents=documents, groups={
        ("ukraine", "deposit-rate", "euro-area"): tuple(documents),
    })
    monkeypatch.setattr(Corpus, "load", lambda _: fixture)
    synthetic = EvidenceGate(M7B)
    question = "What was the mean ECB deposit facility rate for September 2022?"
    policy = scope(event="ukraine", indicator="deposit-rate", cutoff="2022-10-01")
    result = synthetic.assess(question, policy)
    assert result.status == "sufficient"
    assert not result.grounding_claims
    average = result.requirement_coverage[0].derived_rate_average
    assert average.value == Decimal("0.75")
    assert average.weighted_sum == Decimal("22.50") and average.days == 30
    assert {claim.claim_id for claim in average.observations} == set(claims)
    assert all(claim.use == "context_only" and not claim.covered_requirements for claim in average.observations)
    assert synthetic.assess(question, scope(event="ukraine", indicator="deposit-rate", cutoff="2022-09-30")).status == "insufficient"
    assert synthetic.assess(question.replace("mean", "volume-weighted mean"), policy).status == "insufficient"
    assert synthetic.assess(question.replace("mean", "unweighted mean"), policy).status == "insufficient"
    assert synthetic.assess(
        "What was the arithmetic mean of the two published ECB deposit facility rate observations for September 2022?", policy,
    ).status == "insufficient"
    assert synthetic.assess(
        "What was the average of the opening and closing ECB deposit facility rates for September 2022?", policy,
    ).status == "insufficient"
    fixture.groups[("ukraine", "deposit-rate", "euro-area")] = ("4" * 64,)
    assert EvidenceGate(M7B).assess(question, policy).status == "insufficient"
    second = claims[observations[1][0]]
    claims[second.id] = second.model_copy(update={
        "text": "The ECB deposit facility rate averaged 0.75% from 2022-09-01 through 2022-09-30 on a calendar-day-weighted basis.",
        "historical_period": second.historical_period.model_copy(update={"start": "2022-09-01"}),
    })
    published = EvidenceGate(M7B).assess(question, policy)
    assert published.status == "sufficient"
    assert published.grounding_claims[0].claim_id == second.id
    assert published.requirement_coverage[0].derived_rate_average is None


@pytest.mark.parametrize("question", [
    "What was raw euro area quarterly real GDP growth in Q1 2020 prior to seasonal adjustment?",
    "In Q1 2020, what was the euro area real GDP growth rate before it was seasonally adjusted?",
    "What was the not seasonally adjusted real GDP growth rate in Q1 2020?",
    "¿Qué crecimiento registró el PIB real en el primer trimestre de 2020 antes del ajuste estacional?",
    "¿Cuánto creció el PIB real trimestral sin desestacionalizar en 2020 T1?",
    "¿Cuánto cayó el PIB real trimestral de la zona euro en 2020 T1 antes de desestacionalizar la serie?",
    "Give euro area Q1 2020 quarterly real GDP growth before correcting for seasonality.",
    "En el primer trimestre de 2020, ¿cuál fue la variación trimestral del PIB real de la zona euro antes de corregir el efecto estacional?",
])
def test_raw_gdp_requires_unadjusted_evidence(gate, question):
    result = gate.assess(question, scope(indicator="real-gdp-qoq", cutoff="2020-05-15"))
    assert result.status == "insufficient"
    assert not result.grounding_claims
    assert result.requirement_coverage[0].requirement.adjustment == "unadjusted"


@pytest.mark.parametrize(("qualifier", "expected"), [
    ("seasonally adjusted", "sufficient"), ("calendar adjusted", "insufficient"),
    ("", "sufficient"), ("raw and seasonally adjusted", "insufficient"),
])
def test_gdp_adjustment_keeps_positive_unspecified_and_conflicting_requests_distinct(gate, qualifier, expected):
    question = f"What was the euro area real GDP growth rate in Q1 2020 ({qualifier})?"
    result = gate.assess(question, scope(indicator="real-gdp-qoq", cutoff="2020-05-15"))
    assert result.status == expected
    if not qualifier:
        assert result.requirement_coverage[0].requirement.adjustment is None


@pytest.mark.parametrize(("question", "operation", "unit"), [
    ("How much higher was the October 2022 headline annual HICP rate than in the previous month?",
     "rate_difference", "percentage_points"),
    ("¿En cuántos puntos porcentuales fue mayor la tasa interanual del IPCA de octubre de 2022 que la del mes anterior?",
     "percentage_point_difference", "percentage_points"),
    ("By what percentage was the October 2022 annual HICP rate higher than the September 2022 rate?",
     "percentage_change", "percent"),
    ("What was the relative change in annual HICP inflation from September 2022 to October 2022?",
     "percentage_change", "percent"),
])
def test_rate_comparison_grounding_is_the_calculation_with_two_dated_values(gate, question, operation, unit):
    hicp = scope(event="ukraine", cutoff="2022-11-17")
    result = gate.assess(question, hicp)
    assert result.status == "sufficient"
    assert not result.grounding_claims
    row, = result.requirement_coverage
    comparison = row.derived_rate_comparison
    assert row.requirement.operation == comparison.operation == operation
    assert comparison.unit == unit
    if unit == "percentage_points":
        assert comparison.value == Decimal("0.7")
    else:
        assert comparison.value.quantize(Decimal("0.000001")) == Decimal("7.070707")
    assert (comparison.base.reference_month, comparison.base.value_percent) == ("2022-09-01", Decimal("9.9"))
    assert (comparison.target.reference_month, comparison.target.value_percent) == ("2022-10-01", Decimal("10.6"))
    assert all(item.source.use == "context_only" for item in (comparison.base, comparison.target))
    assert gate.assess(question, scope(event="ukraine", cutoff="2022-11-16")).status == "insufficient"


def test_difference_of_averages_does_not_use_observed_rate_levels(gate):
    result = gate.assess(
        "What was the difference between the average annual HICP rates in September 2022 and October 2022?",
        scope(event="ukraine", cutoff="2022-11-17"),
    )
    assert result.status == "insufficient"
    assert not result.grounding_claims
    assert all(row.derived_rate_comparison is None for row in result.requirement_coverage)


@pytest.mark.parametrize(("adjustments", "geographies", "cutoff", "expected"), [
    (("Seasonally adjusted", "Seasonally adjusted"), ("euro area", "euro area"), "2022-12-01", "sufficient"),
    (("Seasonally adjusted", "Unadjusted"), ("euro area", "euro area"), "2022-12-01", "insufficient"),
    (("Unadjusted", "Unadjusted"), ("euro area", "euro area"), "2022-12-01", "sufficient"),
    (("Release vintage", "Release vintage"), ("euro area", "euro area"), "2022-12-01", "insufficient"),
    (("Seasonally adjusted", "Seasonally adjusted"), ("euro area", "euro area (EA19)"), "2022-12-01", "insufficient"),
    (("Seasonally adjusted", "Seasonally adjusted"), ("euro area", "euro area"), "2022-11-16", "insufficient"),
])
def test_separate_rate_observations_must_be_comparable_and_available(monkeypatch, adjustments, geographies, cutoff, expected):
    corpus = Corpus.load(M7B)
    claim = next(row for row in corpus.claims.values() if row.text.startswith("Euro area annual HICP inflation reached 10.6%"))
    document = corpus.documents[claim.document_id]
    observations = (
        ("September", "2022-09-01", "2022-09-30", "2022-10-17", "9.9"),
        ("October", "2022-10-01", "2022-10-31", "2022-11-17", "10.6"),
    )
    claims, documents = {}, {}
    for index, (month, start, end, published, value) in enumerate(observations):
        key, document_id = str(index + 1) * 64, str(index + 3) * 64
        claims[key] = claim.model_copy(update={
            "id": key, "document_id": document_id, "publication_date": published,
            "text": f"Euro area annual HICP inflation was {value}% in {month} 2022.",
            "historical_period": claim.historical_period.model_copy(update={"start": start, "end": end}),
            "qualifiers": adjustments[index], "geography": geographies[index],
        })
        documents[document_id] = document.model_copy(update={
            "id": document_id, "claim_ids": [key], "publication_date": published, "geography": geographies[index],
        })
    fixture = replace(corpus, claims=claims, documents=documents, groups={
        ("ukraine", "hicp-yoy", "euro-area"): tuple(documents),
    })
    monkeypatch.setattr(Corpus, "load", lambda _: fixture)
    gate = EvidenceGate(M7B)
    question = "What was the percentage-point difference in annual HICP from September 2022 to October 2022?"
    result = gate.assess(question, scope(event="ukraine", cutoff=cutoff))
    assert result.status == expected
    assert not result.grounding_claims
    if expected == "sufficient":
        assert result.requirement_coverage[0].derived_rate_comparison.value == Decimal("0.7")


def test_hicp_ellipsis_preserves_measure_units_and_incomplete_coverage(gate):
    hicp = scope(event="ukraine", cutoff="2022-11-17")
    question = (
        "En puntos porcentuales, ¿cuánto aportó la energía al IPCA de octubre de 2022 "
        "y cuál fue la tasa interanual general en porcentaje?"
    )
    result = gate.assess(question, hicp)
    assert result.status == "sufficient"
    assert [row.requirement.measure for row in result.requirement_coverage] == ["hicp_energy", "hicp_annual"]
    assert [row.requirement.unit for row in result.requirement_coverage] == ["percentage_points", "percent"]
    assert gate.assess(
        "For October 2022, give the services and energy contributions to euro-area annual HICP inflation.",
        scope(event="ukraine", cutoff="2022-11-17"),
    ).status == "partial"
    assert [claim.covered_requirements for claim in result.grounding_claims] == [(0,), (1,)]
    partial = gate.assess(question.replace("en porcentaje?", "en puntos del índice?"), hicp)
    assert partial.status == "partial"
    assert [claim.covered_requirements for claim in partial.grounding_claims] == [(0,)]


@pytest.mark.parametrize("question", [
    "How many times as high was the annual HICP rate in October 2022 as in September 2022?",
    "¿Cuántas veces fue mayor la tasa interanual del IPCA de octubre de 2022 que la del mes anterior?",
    "By what factor was the annual HICP inflation rate in October 2022 above its September 2022 level?",
])
def test_rate_ratio_cannot_use_a_level_or_an_additive_difference(gate, question):
    result = gate.assess(question, scope(event="ukraine", cutoff="2022-11-17"))
    assert result.status == "insufficient"
    assert not result.grounding_claims
    assert result.requirement_coverage[0].requirement.operation == "rate_ratio"


@pytest.mark.parametrize("question", [
    "For October 2022, what was the euro area's annual HICP rate, and by how much did it exceed September's?",
    "¿Cuál fue la tasa interanual del IPCA de octubre de 2022 y en cuánto superó la de septiembre?",
    "Give the annual HICP rate for October 2022 and its change from the previous month.",
    "For October 2022, what was the annual HICP rate and by what margin did it exceed September’s?",
])
def test_level_and_elliptical_comparison_are_independent_requirements(gate, question):
    result = gate.assess(question, scope(event="ukraine", cutoff="2022-11-17"))
    assert result.status == "sufficient"
    level, difference = result.requirement_coverage
    assert level.requirement.operation == "observed_level"
    assert difference.derived_rate_comparison.value == Decimal("0.7")
    assert len(result.grounding_claims) == 1
    assert result.grounding_claims[0].covered_requirements == (level.index,)


def test_prenominal_energy_contribution_keeps_its_own_unit(gate):
    result = gate.assess(
        "For October 2022, give the energy contribution to euro-area annual HICP inflation "
        "in percentage points and the headline rate in percent.",
        scope(event="ukraine", cutoff="2022-11-17"),
    )
    assert result.status == "sufficient"
    assert [row.requirement.measure for row in result.requirement_coverage] == ["hicp_energy", "hicp_annual"]
    assert [row.requirement.unit for row in result.requirement_coverage] == ["percentage_points", "percent"]


def test_absolute_difference_is_distinct_from_signed_difference(gate):
    hicp = scope(event="ukraine", cutoff="2022-11-17")
    question = "What was the absolute difference in percentage points between October 2022 and September 2022 annual HICP rates?"
    absolute = gate.assess(question, hicp).requirement_coverage[0]
    signed = gate.assess(question.replace("absolute difference", "signed difference"), hicp).requirement_coverage[0]
    assert absolute.requirement.comparison.absolute
    assert absolute.derived_rate_comparison.value == Decimal("0.7")
    assert not signed.requirement.comparison.absolute
    assert signed.derived_rate_comparison.value == Decimal("-0.7")


@pytest.mark.parametrize(("publication", "expected"), [(16, "insufficient"), (17, "sufficient")])
def test_comparison_ellipsis_preserves_requested_vintage(gate, publication, expected):
    question = (
        f"Based on the {publication} November 2022 release, what was annual HICP inflation in October 2022 "
        "and by how much did it exceed September’s?"
    )
    result = gate.assess(question, scope(event="ukraine", cutoff="2022-11-17"))
    assert result.status == expected
    level, difference = result.requirement_coverage
    assert level.requirement.timing.publication == difference.requirement.timing.publication
    if expected == "sufficient":
        assert difference.derived_rate_comparison.value == Decimal("0.7")
        assert all(item.source.publication_date == "2022-11-17" for item in (
            difference.derived_rate_comparison.base, difference.derived_rate_comparison.target,
        ))
    else:
        assert not result.grounding_claims and difference.derived_rate_comparison is None
