"""Approved evidence selection and predicate coverage, isolated from macro_ai."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path
import re

from backend.m7c.corpus import Corpus, Scope
from backend.macro_data.contracts import CATALOG
from scripts.m7b_schema import Claim, Institution

from .comparisons import hicp_observations
from .contracts import (
    AssessedScope, DerivedRateAverage, DerivedRateComparison, EvidenceAssessment, Limitation, OfficialMovement,
    RateObservation, RequirementCoverage, SelectedClaim, Sufficiency, Use,
)
from .predicates import Intent, Measure, Population, Predicate, Requirement, When, claim_predicates, interpret, measure_variant, normalize
from .qualifiers import statistical_variant
from .ranking import LexicalRanker, Ranker

LIMIT = 5
RankerFactory = Callable[[Mapping[str, str]], Ranker]
MEASURES = {
    "hicp-yoy": {"hicp_annual", "hicp_energy", "hicp_food", "hicp_energy_rate", "hicp_diagnosis"},
    "real-gdp-qoq": {"gdp_quarterly", "gdp_consumption"},
    "deposit-rate": {"deposit_decision", "deposit_effective", "deposit_in_force", "deposit_intention", "deposit_move",
                     "app_schedule", "ltro_pricing", "tltro_pricing"},
    "m3-stock": {"m3_annual", "m3_stock", "m1_annual", "m1_contribution"},
    "eur-usd": {"spot_move", "spot_level", "swap_liquidity"},
}
PROXY_MEASURES = {"ai_enterprise", "ai_language", "ai_automation", "ai_household",
                  "ai_purpose", "ai_tasks", "ai_hypothesis"}
SERIES_CONTEXT_MEASURES = {
    "series_change", "spot_move", "hicp_annual", "gdp_quarterly", "m3_stock", "deposit_decision", "deposit_move",
}
RATE_COMPARISONS = {"rate_difference", "percentage_point_difference", "percentage_change"}


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


def _dimensions_match(requirement: Requirement, claim: Claim, predicate: Predicate, institution: Institution) -> bool:
    if (requirement.measure != predicate.measure or (requirement.unit and requirement.unit not in predicate.units)
            or (requirement.institution and requirement.institution != institution)
            or (requirement.modality and requirement.modality != claim.type)
            or (requirement.area_definition and requirement.area_definition not in claim.geography.lower())
            or not _population_matches(requirement.population, predicate.population)
            or (requirement.denominator is not None and requirement.denominator != predicate.denominator)
            or requirement.subgroup != predicate.subgroup or requirement.purpose != predicate.purpose
            or (requirement.technology is not None and requirement.technology != predicate.technology)
            or (requirement.variant or measure_variant(requirement.measure)) != predicate.variant
            or predicate.variant == "other" or predicate.subgroup == "other" or predicate.purpose == "other"
            or predicate.adjustment == "other"
            or requirement.polarity != predicate.polarity
            or (requirement.adjustment is not None
                and (requirement.adjustment == "other" or requirement.adjustment != predicate.adjustment))
            or (requirement.flash and not predicate.flash)):
        return False
    return True


def _bounds(when: When, fallback_year: int) -> tuple[date, date] | None:
    try:
        return when.bounds(fallback_year)
    except ValueError:
        return None


def _policy_observation(requirement: Requirement, predicate: Predicate, cutoff: date) -> bool:
    coverage = predicate.rate_coverage
    if coverage is None or coverage.end > cutoff or not requirement.timing.reference:
        return False
    if requirement.statistic != "point":
        if (requirement.average_method or "calendar_day_weighted") != predicate.average_method:
            return False
        return all(_bounds(when, coverage.start.year) == (coverage.start, coverage.end)
                   for when in requirement.timing.reference)
    return all(when.day is not None and (bounds := _bounds(when, coverage.start.year)) is not None
               and coverage.start <= bounds[0] <= coverage.end for when in requirement.timing.reference)


def _matches(
    requirement: Requirement, claim: Claim, predicate: Predicate, institution: Institution, *, cutoff: date, whole_period: bool,
) -> bool:
    if requirement.operation in RATE_COMPARISONS or requirement.operation == "rate_ratio":
        return False
    if not _dimensions_match(requirement, claim, predicate, institution):
        return False
    if requirement.statistic != predicate.statistic or requirement.statistic == "other":
        return False
    if requirement.premise is not None and requirement.premise not in {
        Decimal(value.replace(",", ".")) for value in re.findall(r"(-?\d+(?:[.,]\d+)?)\s*%", claim.text)
    }:
        return False
    timing = requirement.timing
    published = date.fromisoformat(claim.publication_date)
    if published > cutoff:
        return False
    if not all(when.includes_day(published) for when in timing.publication):
        return False
    if timing.effective:
        if predicate.effective_date is None or not all(when.includes_day(predicate.effective_date) for when in timing.effective):
            return False
    if requirement.measure == "deposit_in_force":
        return _policy_observation(requirement, predicate, cutoff)
    if requirement.measure in {"deposit_decision", "deposit_effective"}:
        if requirement.measure == "deposit_effective" and (
            predicate.effective_date is None or (predicate.effective_date > cutoff and not requirement.announced)
        ):
            return False
        decision = predicate.decision_date
        return decision is not None and decision <= cutoff and not requirement.revised and all(
            when.includes_day(decision) for when in timing.reference
        )
    if requirement.measure == "deposit_intention":
        return all(any(
            (value.year or published.year, value.month, value.day) ==
            (when.year or published.year, when.month, when.day)
            for value in predicate.timing.reference if not value.includes_day(published)
        ) for when in timing.reference)
    if requirement.measure in {"app_schedule", "ai_tasks", "ai_hypothesis", "ltro_pricing", "tltro_pricing"}:
        return not requirement.revised and all(when.includes_day(published) for when in timing.reference)
    if claim.type == "observed" and date.fromisoformat(claim.historical_period.end) > cutoff:
        return False
    if requirement.revised and requirement.measure == "m3_annual":
        start = date.fromisoformat(claim.historical_period.start)
        if not predicate.previous_reference or not timing.reference:
            return False
        if not all(_prior_period(when, start) for when in timing.reference):
            return False
    elif requirement.revised and (requirement.measure != "gdp_quarterly" or not predicate.revised or predicate.flash):
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
    if requirement.operation in RATE_COMPARISONS or requirement.operation == "rate_ratio":
        return False
    if requirement.statistic not in {"point", "change"} or any((
        requirement.subgroup, requirement.denominator, requirement.purpose, requirement.technology,
    )):
        return False
    catalog = CATALOG[observation.indicator]
    variant = {"deposit-rate": "nominal", "hicp-yoy": "headline", "real-gdp-qoq": "real"}.get(observation.indicator)
    if requirement.variant is not None and requirement.variant != variant:
        return False
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
            unmatched = intent.unmatched_parts or (question,)
            coverage = tuple(RequirementCoverage(index=index, requirement=None, unmatched_text=part, status="uncovered")
                             for index, part in enumerate(unmatched))
            return self._result(scope, "insufficient", [*limitations, "unsupported_question"], (), movement, coverage)
        allowed = MEASURES[scope.indicator] | {"series_change"}
        if scope.event == "genai" and scope.indicator == "real-gdp-qoq":
            allowed |= PROXY_MEASURES
        if any(item.measure not in allowed or item.indicator not in {None, scope.indicator}
               or item.geography not in {None, scope.geography} for item in intent.requirements):
            return self._result(scope, "insufficient", [*limitations, "unsupported_question"], (), movement,
                                self._requirements(intent))
        if any(not all(day.includes_day(date.fromisoformat(scope.cutoff)) for day in item.timing.as_of)
               for item in intent.requirements):
            return self._result(scope, "insufficient", [*limitations, "unsupported_question"], (), movement,
                                self._requirements(intent))

        eligible = self._eligible(scope)
        if intent.causal:
            return self._assess_causality(question, scope, intent, eligible, movement, limitations)
        coverage = self._coverage(scope, intent, eligible)
        matches = tuple(key for key in eligible if coverage[key])
        series_coverage = {i for i, requirement in enumerate(intent.requirements)
                           if requirement.measure in {"series_change", "spot_move"} and _series_matches(requirement, movement)}
        ranked = self._order(question, matches)
        return self._assess_coverage(scope, intent, eligible, coverage, ranked, series_coverage, movement, limitations)

    def _coverage(self, scope: Scope, intent: Intent, eligible: tuple[str, ...]) -> dict[str, tuple[int, ...]]:
        coverage = {}
        cutoff = date.fromisoformat(scope.cutoff)
        for key in eligible:
            claim = self._corpus.claims[key]
            document = self._corpus.documents[claim.document_id]
            is_proxy = document.indicators[scope.indicator] == "proxy"
            coverage[key] = tuple(
                index for index, requirement in enumerate(intent.requirements)
                if (not is_proxy or requirement.measure in PROXY_MEASURES)
                and any(_matches(requirement, claim, predicate, document.institution, cutoff=cutoff,
                                 whole_period=requirement.whole_period or intent.whole_period)
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
            replace(
                item, unit=None, premise=None, polarity="affirmative", operation=None, comparison=None,
                statistic="point" if item.measure == "hicp_annual" and item.statistic == "change" else item.statistic,
            )
            for item in intent.requirements
        ))
        coverage = self._coverage(scope, contextual, eligible)
        context = tuple(key for key in eligible if coverage[key]) or self._causal_context(contextual, eligible, scope)
        causal_observation = any(_series_matches(item, movement) for item in contextual.requirements
                                 if item.measure in SERIES_CONTEXT_MEASURES)
        limitations.append("causal_identification_unavailable")
        if context or causal_observation:
            ranked = self._order(question, context)
            selected = tuple(self._selected(key, scope, "context_only", ()) for key in ranked[:LIMIT])
            return self._result(scope, "unsupported_causal", limitations, selected, movement, self._requirements(intent))
        return self._result(scope, "insufficient", limitations, (), movement, self._requirements(intent))

    @staticmethod
    def _requirements(intent: Intent) -> tuple[RequirementCoverage, ...]:
        unknown = intent.unmatched_parts or (("Uninterpreted requirement",) if intent.unknown_parts else ())
        return (
            *(RequirementCoverage(index=index, requirement=requirement, status="uncovered")
              for index, requirement in enumerate(intent.requirements)),
            *(RequirementCoverage(index=index, requirement=None, unmatched_text=part, status="uncovered")
              for index, part in enumerate(unknown, start=len(intent.requirements))),
        )

    def _derived_average(
        self, scope: AssessedScope, requirement: Requirement, eligible: tuple[str, ...],
    ) -> DerivedRateAverage | None:
        if (requirement.measure != "deposit_in_force" or requirement.statistic != "average"
                or requirement.average_method == "other"):
            return None
        timing = requirement.timing
        if len(timing.reference) != 1 or timing.publication or timing.effective or requirement.revised or requirement.premise:
            return None
        when = timing.reference[0]
        if when.year is None or when.day is not None or (bounds := _bounds(when, when.year)) is None:
            return None
        start, end = bounds
        if end > date.fromisoformat(scope.cutoff):
            return None
        spans = []
        for key in eligible:
            claim = self._corpus.claims[key]
            document = self._corpus.documents[claim.document_id]
            if document.indicators[scope.indicator] != "direct" or claim.publication_date > scope.cutoff:
                continue
            for predicate in self._predicates[key]:
                rate = predicate.rate_coverage
                if (rate is not None and predicate.statistic == "point" and rate.end >= start and rate.start <= end
                        and _dimensions_match(requirement, claim, predicate, document.institution)):
                    spans.append((max(rate.start, start), min(rate.end, end), rate.value, key))
        cursor = start.toordinal()
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            weighted = Decimal(0)
            for left, right, value, _ in sorted(spans):
                if left.toordinal() != cursor:
                    return None  # A gap, duplicate vintage, or overlap leaves coverage unresolved.
                cursor = right.toordinal() + 1
                weighted += value * ((right - left).days + 1)
            if cursor != end.toordinal() + 1:
                return None
            days = (end - start).days + 1
            return DerivedRateAverage(start=start.isoformat(), end=end.isoformat(), value=weighted / days,
                                      weighted_sum=weighted, days=days, observations=tuple(
                                          self._selected(key, scope, "context_only", ()) for *_, key in sorted(spans)
                                      ))

    def _derived_rate_comparison(
        self, scope: AssessedScope, requirement: Requirement, eligible: tuple[str, ...],
    ) -> DerivedRateComparison | None:
        operation = requirement.operation
        comparison = requirement.comparison
        if (requirement.measure != "hicp_annual" or operation not in RATE_COMPARISONS or comparison is None
                or requirement.statistic != "change" or requirement.premise is not None or requirement.revised
                or requirement.whole_period or requirement.timing.effective):
            return None
        months = (comparison.base, comparison.target)
        cutoff = date.fromisoformat(scope.cutoff)
        if months[0] == months[1] or any(
            when.year is None or when.month is None or when.day is not None or when.quarter is not None
            or (bounds := _bounds(when, cutoff.year)) is None or bounds[1] > cutoff for when in months
        ):
            return None
        expected_unit = "percent" if operation == "percentage_change" else "percentage_points"
        if requirement.unit not in {None, expected_unit}:
            return None
        level = replace(requirement, unit="percent", modality="observed", statistic="point",
                        operation="observed_level", comparison=None)
        if requirement.modality not in {None, "observed"}:
            return None
        candidates = {month: [] for month in months}
        for key in eligible:
            claim = self._corpus.claims[key]
            document = self._corpus.documents[claim.document_id]
            published = date.fromisoformat(claim.publication_date)
            if (document.indicators[scope.indicator] != "direct" or published > cutoff
                    or not all(when.includes_day(published) for when in requirement.timing.publication)):
                continue
            for predicate in self._predicates[key]:
                if predicate.statistic != "point" or not _dimensions_match(level, claim, predicate, document.institution):
                    continue
                if statistical_variant(normalize(claim.qualifiers)) not in {None, predicate.variant}:
                    continue
                definition = (claim.geography, document.institution, predicate.variant, predicate.adjustment)
                for month, value in hicp_observations(claim):
                    if month in candidates and month.bounds(published.year)[1] <= published:
                        candidates[month].append((value, key, definition))
        if any(len(rows) != 1 for rows in candidates.values()):
            return None
        (base_value, base_key, base_definition), (target_value, target_key, target_definition) = (
            candidates[month][0] for month in months
        )
        if base_definition != target_definition or (base_key != target_key and base_definition[-1] is None):
            return None
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            value = target_value - base_value
            if (comparison.direction == "higher" and value <= 0) or (comparison.direction == "lower" and value >= 0):
                return None
            if comparison.absolute:
                value = abs(value)
            if operation == "percentage_change":
                if base_value <= 0:
                    return None
                value = value * 100 / base_value
        base, target = (
            RateObservation(reference_month=month.bounds(cutoff.year)[0].isoformat(), value_percent=rate,
                            source=self._selected(key, scope, "context_only", ()))
            for month, rate, key in zip(months, (base_value, target_value), (base_key, target_key), strict=True)
        )
        return DerivedRateComparison(
            operation=operation, unit=expected_unit, value=value, base=base, target=target, absolute=comparison.absolute,
        )

    def _assess_coverage(
        self, scope: AssessedScope, intent: Intent, eligible: tuple[str, ...],
        coverage: dict[str, tuple[int, ...]], ranked: tuple[str, ...],
        series_coverage: set[int], movement: OfficialMovement | None, limitations: list[Limitation],
    ) -> EvidenceAssessment:
        outcomes = list(self._requirements(intent))
        chosen: dict[str, list[int]] = {}
        context = []
        limit_reached = False
        for index, requirement in enumerate(intent.requirements):
            row = outcomes[index]
            if index in series_coverage:
                outcomes[index] = row.model_copy(update={"status": "supported", "official_movement": True})
                continue
            answers = tuple(key for key in ranked if index in coverage[key])
            if not answers:
                if requirement.operation in RATE_COMPARISONS:
                    comparison = self._derived_rate_comparison(scope, requirement, eligible)
                    if comparison is not None:
                        outcomes[index] = row.model_copy(update={"status": "supported", "derived_rate_comparison": comparison})
                    continue
                derived = self._derived_average(scope, requirement, eligible)
                if derived:
                    outcomes[index] = row.model_copy(update={"status": "supported", "derived_rate_average": derived})
                continue
            periods = {(self._corpus.claims[key].historical_period.start, self._corpus.claims[key].historical_period.end)
                       for key in answers}
            ambiguous_answer = len(answers) > 1 and (
                requirement.measure == "deposit_in_force"
                or (not requirement.timing.reference and not requirement.timing.publication and len(periods) > 1)
                or self._ambiguous_vintage(answers, requirement)
            )
            if ambiguous_answer:
                outcomes[index] = row.model_copy(update={"status": "ambiguous"})
                context.extend(answers)
                continue
            key = answers[0]
            if key not in chosen and len(chosen) == LIMIT:
                limit_reached = True
                continue
            chosen.setdefault(key, []).append(index)
            outcomes[index] = row.model_copy(update={"status": "supported", "claim_ids": (key,)})
        if limit_reached:
            limitations.append("claim_limit_reached")
        selected = (
            *(self._selected(key, scope, "supports_requested_fact",
                             tuple(dict.fromkeys(intent.requirements[i].measure for i in indices)), tuple(indices))
              for key, indices in chosen.items()),
            *(self._selected(key, scope, "context_only", ())
              for key in tuple(dict.fromkeys(key for key in context if key not in chosen))[:LIMIT - len(chosen)]),
        )
        ambiguous = any(row.status == "ambiguous" for row in outcomes)
        if ambiguous:
            limitations.append("ambiguous_period_or_vintage")
        if not ambiguous and all(row.status == "uncovered" for row in outcomes):
            limitations.append("no_approved_claim_matches")
            status: Sufficiency = "insufficient"
        elif any(row.status != "supported" for row in outcomes):
            limitations.append("incomplete_predicate_coverage")
            status = "partial"
        else:
            status = "sufficient"
        return self._result(scope, status, limitations, selected, movement, tuple(outcomes))

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

    def _selected(
        self, key: str, scope: Scope, use: Use, measures: tuple[Measure, ...], requirements: tuple[int, ...] = (),
    ) -> SelectedClaim:
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
            covered_requirements=requirements,
        )

    def _result(
        self, scope: AssessedScope, status: Sufficiency, limitations: list[Limitation], claims: tuple[SelectedClaim, ...],
        movement: OfficialMovement | None, coverage: tuple[RequirementCoverage, ...] = (),
    ) -> EvidenceAssessment:
        if any(claim.relation == "proxy" for claim in claims):
            limitations.append("proxy_for_indicator")
        if any(claim.relation == "context" for claim in claims):
            limitations.append("context_for_indicator")
        return EvidenceAssessment(
            corpus_sha256=self.corpus_sha256, scope=scope, status=status,
            limitations=tuple(limitations), selected_claims=claims, official_movement=movement,
            requirement_coverage=coverage,
        )
