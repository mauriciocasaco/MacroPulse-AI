"""Approved evidence selection and predicate coverage, isolated from macro_ai."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from backend.m7c.corpus import Corpus, Scope
from backend.macro_data.contracts import CATALOG
from scripts.m7b_schema import Claim, Institution

from .contracts import AssessedScope, EvidenceAssessment, Limitation, OfficialMovement, SelectedClaim, Sufficiency, Use
from .predicates import Intent, Measure, Population, Predicate, Requirement, When, claim_predicates, interpret
from .ranking import LexicalRanker, Ranker

LIMIT = 5
RankerFactory = Callable[[Mapping[str, str]], Ranker]
MEASURES = {
    "hicp-yoy": {"hicp_annual", "hicp_energy", "hicp_food", "hicp_energy_rate", "hicp_diagnosis"},
    "real-gdp-qoq": {"gdp_quarterly", "gdp_consumption"},
    "deposit-rate": {"deposit_decision", "deposit_effective", "deposit_intention", "deposit_move",
                     "app_schedule", "ltro_pricing", "tltro_pricing"},
    "m3-stock": {"m3_annual", "m3_stock", "m1_annual", "m1_contribution"},
    "eur-usd": {"spot_move", "spot_level", "swap_liquidity"},
}
PROXY_MEASURES = {"ai_enterprise", "ai_language", "ai_automation", "ai_household",
                  "ai_purpose", "ai_tasks", "ai_hypothesis"}
SERIES_CONTEXT_MEASURES = {
    "series_change", "spot_move", "hicp_annual", "gdp_quarterly", "m3_stock", "deposit_decision", "deposit_move",
}


def _ref_match(when: When, claim: Claim, predicate: Predicate, *, whole_period: bool) -> bool:
    start = date.fromisoformat(claim.historical_period.start)
    end = date.fromisoformat(claim.historical_period.end)
    try:
        left, right = when.bounds(start.year)
    except ValueError:
        return False
    if whole_period:
        return start <= left and end >= right
    if when.day is not None:
        # A monthly or quarterly value never establishes a daily observation.
        return start == end == left
    if when.month is None and when.quarter is None:
        return left <= start <= end <= right
    if left <= start <= end <= right:
        return True
    return any(
        (value.year or start.year, value.month, value.quarter) == (when.year or start.year, when.month, when.quarter)
        for value in predicate.timing.reference if value.day is None
    )


def _matches(
    requirement: Requirement, claim: Claim, predicate: Predicate, institution: Institution, *, whole_period: bool,
) -> bool:
    if (requirement.measure != predicate.measure or (requirement.unit and requirement.unit not in predicate.units)
            or (requirement.institution and requirement.institution != institution)
            or (requirement.modality and requirement.modality != claim.type)
            or (requirement.area_definition and requirement.area_definition not in claim.geography.lower())
            or not _population_matches(requirement.population, predicate.population)
            or requirement.polarity != predicate.polarity
            or (requirement.adjustment is not None
                and (requirement.adjustment == "other" or requirement.adjustment != predicate.adjustment))
            or (requirement.flash and not predicate.flash)):
        return False
    if requirement.premise is not None and requirement.premise not in {
        Decimal(value.replace(",", ".")) for value in re.findall(r"(-?\d+(?:[.,]\d+)?)\s*%", claim.text)
    }:
        return False
    timing = requirement.timing
    published = date.fromisoformat(claim.publication_date)
    if not all(when.includes_day(published) for when in timing.publication):
        return False
    if timing.effective:
        if not all(any(
            value.day is not None and when.includes_day(value.bounds(published.year)[0])
            for value in predicate.timing.effective
        ) for when in timing.effective):
            return False
    if requirement.measure == "deposit_intention":
        return all(any(
            (value.year or published.year, value.month, value.day) ==
            (when.year or published.year, when.month, when.day)
            for value in predicate.timing.reference if not value.includes_day(published)
        ) for when in timing.reference)
    if requirement.measure in {"deposit_decision", "deposit_effective", "app_schedule", "ai_tasks", "ai_hypothesis",
                               "ltro_pricing", "tltro_pricing"}:
        return not requirement.revised and all(when.includes_day(published) for when in timing.reference)
    if requirement.revised and requirement.measure == "m3_annual":
        start = date.fromisoformat(claim.historical_period.start)
        if not predicate.previous_reference or not timing.reference:
            return False
        if not all(_prior_period(when, start) for when in timing.reference):
            return False
    elif requirement.revised and (requirement.measure != "gdp_quarterly" or predicate.flash or not timing.publication):
        return False
    return all(_ref_match(when, claim, predicate, whole_period=whole_period) for when in timing.reference)


def _population_matches(requested: Population | None, reported: Population | None) -> bool:
    return requested is None or requested == reported or (requested, reported) in {
        ("enterprises", "enterprises_10_plus"), ("persons", "persons_16_74"),
    }


def _prior_period(when: When, start: date) -> bool:
    try:
        return when.bounds(start.year)[1] < start
    except ValueError:
        return False


def _series_matches(requirement: Requirement, observation: OfficialMovement | None) -> bool:
    if observation is None or (requirement.unit and observation.unit != requirement.unit):
        return False
    if requirement.modality not in {None, "observed"} or requirement.population is not None:
        return False
    catalog = CATALOG[observation.indicator]
    if requirement.area_definition and requirement.area_definition.removeprefix("ea") not in catalog.geography:
        return False
    if requirement.polarity != "affirmative" or (requirement.adjustment and requirement.adjustment != catalog.adjustment):
        return False
    if requirement.revised or requirement.flash or requirement.timing.publication or requirement.timing.effective:
        return False
    issuer = "Eurostat" if observation.indicator in {"hicp-yoy", "real-gdp-qoq"} else "BCE"
    if requirement.institution and requirement.institution != issuer:
        return False
    first = date.fromisoformat(observation.first_observed_on)
    last = date.fromisoformat(observation.last_observed_on)
    period = requirement.timing.reference
    if not period:
        return True
    if len(period) == 1:
        return period[0].includes_day(first) and period[0].includes_day(last)
    return len(period) == 2 and period[0].includes_day(first) and period[1].includes_day(last)


class EvidenceGate:
    def __init__(self, corpus_directory: Path, *, ranker: RankerFactory = LexicalRanker):
        self._corpus = Corpus.load(corpus_directory)
        self._predicates = {key: claim_predicates(claim) for key, claim in self._corpus.claims.items()}
        self._ranker = ranker({key: claim.text for key, claim in self._corpus.claims.items()})

    @property
    def corpus_sha256(self) -> str:
        return self._corpus.fingerprint

    def _eligible(self, scope: Scope) -> tuple[str, ...]:
        return tuple(key.removeprefix("claim:") for key in self._corpus.eligible(scope) if key.startswith("claim:"))

    def rank_candidates(self, question: str, scope: Scope) -> tuple[SelectedClaim, ...]:
        """Diagnostic retrieval for offline comparisons, with no claim of support."""
        question = self._question(question)
        scope = AssessedScope.model_validate(scope.model_dump())
        ranked = self._order(question, self._eligible(scope))
        return tuple(self._selected(key, scope, "context_only", ()) for key in ranked[:LIMIT])

    def assess(self, question: str, scope: Scope, *, movement: OfficialMovement | None = None) -> EvidenceAssessment:
        question = self._question(question)
        scope = AssessedScope.model_validate(scope.model_dump())
        return self.assess_intent(question, scope, interpret(question, scope), movement=movement)

    def assess_intent(
        self, question: str, scope: Scope, intent: Intent, *, movement: OfficialMovement | None = None,
    ) -> EvidenceAssessment:
        """Structured entry point for a trusted, separately validated intent adapter."""
        question = self._question(question)
        scope = AssessedScope.model_validate(scope.model_dump())
        limitations: list[Limitation] = []
        if movement is not None and (
            (movement.indicator, movement.event, movement.geography) != (scope.indicator, scope.event, scope.geography)
            or movement.available_on is None or movement.available_on > scope.cutoff
        ):
            movement = None
            limitations.append("official_vintage_unverified_or_out_of_scope")
        if intent.unsupported or not intent.requirements:
            return self._result(scope, "insufficient", [*limitations, "unsupported_question"], (), movement)
        allowed = MEASURES[scope.indicator] | {"series_change"}
        if scope.event == "genai" and scope.indicator == "real-gdp-qoq":
            allowed |= PROXY_MEASURES
        if any(item.measure not in allowed for item in intent.requirements):
            return self._result(scope, "insufficient", [*limitations, "unsupported_question"], (), movement)
        if any(not all(day.includes_day(date.fromisoformat(scope.cutoff)) for day in item.timing.as_of)
               for item in intent.requirements):
            return self._result(scope, "insufficient", [*limitations, "unsupported_question"], (), movement)

        eligible = self._eligible(scope)
        if intent.causal:
            return self._assess_causality(question, scope, intent, eligible, movement, limitations)
        coverage = self._coverage(scope, intent, eligible)
        matches = tuple(key for key in eligible if coverage[key])
        series_coverage = {i for i, requirement in enumerate(intent.requirements)
                           if requirement.measure in {"series_change", "spot_move"} and _series_matches(requirement, movement)}
        ranked = self._order(question, matches)
        return self._assess_coverage(scope, intent, coverage, ranked, series_coverage, movement, limitations)

    def _coverage(self, scope: Scope, intent: Intent, eligible: tuple[str, ...]) -> dict[str, tuple[int, ...]]:
        coverage = {}
        for key in eligible:
            claim = self._corpus.claims[key]
            document = self._corpus.documents[claim.document_id]
            is_proxy = document.indicators[scope.indicator] == "proxy"
            coverage[key] = tuple(
                index for index, requirement in enumerate(intent.requirements)
                if (not is_proxy or requirement.measure in PROXY_MEASURES)
                and any(_matches(requirement, claim, predicate, document.institution, whole_period=intent.whole_period)
                        for predicate in self._predicates[key])
            )
        return coverage

    def _assess_causality(
        self, question: str, scope: AssessedScope, intent: Intent, eligible: tuple[str, ...],
        movement: OfficialMovement | None, limitations: list[Limitation],
    ) -> EvidenceAssessment:
        # Observations can give context for a causal question even when their
        # units differ from the requested causal effect.
        contextual = replace(intent, requirements=tuple(
            replace(item, unit=None, premise=None, polarity="affirmative") for item in intent.requirements
        ))
        coverage = self._coverage(scope, contextual, eligible)
        context = tuple(key for key in eligible if coverage[key]) or self._causal_context(contextual, eligible, scope)
        causal_observation = any(_series_matches(item, movement) for item in contextual.requirements
                                 if item.measure in SERIES_CONTEXT_MEASURES)
        limitations.append("causal_identification_unavailable")
        if context or causal_observation:
            ranked = self._order(question, context)
            selected = tuple(self._selected(key, scope, "context_only", ()) for key in ranked[:LIMIT])
            return self._result(scope, "unsupported_causal", limitations, selected, movement)
        return self._result(scope, "insufficient", limitations, (), movement)

    def _assess_coverage(
        self, scope: AssessedScope, intent: Intent, coverage: dict[str, tuple[int, ...]], ranked: tuple[str, ...],
        series_coverage: set[int], movement: OfficialMovement | None, limitations: list[Limitation],
    ) -> EvidenceAssessment:
        covered = series_coverage.copy()
        chosen = []
        ambiguous = False
        for index, requirement in enumerate(intent.requirements):
            answers = tuple(key for key in ranked if index in coverage[key])
            if index in covered or not answers:
                continue
            periods = {(self._corpus.claims[key].historical_period.start, self._corpus.claims[key].historical_period.end)
                       for key in answers}
            ambiguous_answer = len(answers) > 1 and (
                (not requirement.timing.reference and not requirement.timing.publication and len(periods) > 1)
                or self._ambiguous_vintage(answers, requirement)
            )
            ambiguous |= ambiguous_answer
            for key in (answers if ambiguous_answer else answers[:1]):
                if key not in chosen:
                    chosen.append(key)
                covered.update(coverage[key])
        if len(chosen) > LIMIT:
            limitations.append("claim_limit_reached")
        selected = tuple(
            self._selected(key, scope, "context_only" if ambiguous else "supports_requested_fact",
                           tuple(intent.requirements[i].measure for i in coverage[key]))
            for key in chosen[:LIMIT]
        )
        if ambiguous:
            limitations.append("ambiguous_period_or_vintage")
        if not covered:
            limitations.append("no_approved_claim_matches")
            status: Sufficiency = "insufficient"
        elif len(covered) < len(intent.requirements) or len(chosen) > LIMIT or ambiguous or intent.unknown_parts:
            limitations.append("incomplete_predicate_coverage")
            status = "partial"
        else:
            status = "sufficient"
        return self._result(scope, status, limitations, selected, movement)

    def _causal_context(self, intent: Intent, eligible: tuple[str, ...], scope: Scope) -> tuple[str, ...]:
        if scope.event != "genai" or not any(item.measure == "gdp_quarterly" for item in intent.requirements):
            return ()
        return tuple(
            key for key in eligible if self._corpus.documents[self._corpus.claims[key].document_id].indicators[scope.indicator] == "proxy"
            and any(p.measure in PROXY_MEASURES and all(
                _ref_match(day, self._corpus.claims[key], p, whole_period=False)
                for day in requirement.timing.reference
            ) for p in self._predicates[key] for requirement in intent.requirements if requirement.measure == "gdp_quarterly")
        )

    def _ambiguous_vintage(self, keys: tuple[str, ...], requirement: Requirement) -> bool:
        if requirement.timing.publication or requirement.revised or requirement.flash:
            return False
        documents = {self._corpus.claims[key].document_id for key in keys}
        return len(documents) > 1

    @staticmethod
    def _question(question: str) -> str:
        if not isinstance(question, str) or not (1 <= len(question.strip()) <= 2000):
            raise ValueError("Query must contain between 1 and 2000 characters")
        return question.strip()

    def _order(self, question: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
        ids = tuple(row.passage_id for row in self._ranker.rank(question, candidates))
        if len(ids) != len(candidates) or set(ids) != set(candidates):
            raise ValueError("Claim ranker must order exactly the eligible claim IDs")
        return ids

    def _selected(self, key: str, scope: Scope, use: Use, measures: tuple[Measure, ...]) -> SelectedClaim:
        claim = self._corpus.claims[key]
        document = self._corpus.documents[claim.document_id]
        return SelectedClaim(
            claim_id=key, document_id=document.id, title=document.title, institution=document.institution,
            source_url=claim.primary_source, attribution=document.rights.attribution, rights_url=document.rights.policy_url,
            source_payload_sha256=document.source_payload_sha256, source_block_sha256=claim.source_block_sha256,
            locator=claim.locator, text=claim.text, qualifiers=claim.qualifiers, claim_type=claim.type,
            publication_date=claim.publication_date, reference_start=claim.historical_period.start,
            reference_end=claim.historical_period.end, indicator=scope.indicator, event=scope.event,
            geography=scope.geography, relation=document.indicators[scope.indicator], use=use, covered_measures=measures,
        )

    def _result(
        self, scope: AssessedScope, status: Sufficiency, limitations: list[Limitation], claims: tuple[SelectedClaim, ...],
        movement: OfficialMovement | None,
    ) -> EvidenceAssessment:
        if any(claim.relation == "proxy" for claim in claims):
            limitations.append("proxy_for_indicator")
        if any(claim.relation == "context" for claim in claims):
            limitations.append("context_for_indicator")
        return EvidenceAssessment(
            corpus_sha256=self.corpus_sha256, scope=scope, status=status,
            limitations=tuple(limitations), selected_claims=claims, official_movement=movement,
        )
