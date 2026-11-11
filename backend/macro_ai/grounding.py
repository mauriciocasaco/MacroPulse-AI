"""Validate generated prose against a small grammar of evidence-bound propositions."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import Claim, GeminiDraft, ObservedChange


_SUBJECTS = {
    "eur-usd": ("the value of the euro in US dollars", "EUR/USD", "the euro"),
    "m3-stock": ("the M3 money stock", "the amount of broad money in the euro area"),
    "deposit-rate": ("the ECB deposit rate", "the ECB's deposit facility rate"),
    "real-gdp-qoq": ("quarterly real GDP growth", "the euro area's quarterly growth rate"),
    "hicp-yoy": ("annual euro area inflation", "the euro area's headline inflation rate"),
}
_TIME = ("over the selected period", "between the first and last observations", "over this window")
_TRANSITIONS = ("For context", "As background")
# Institutional language is reviewed alongside its exact source proposition. A
# retrieved chunk must contain that proposition to authorize the wording below.
# Keep each predicate attached to its actor, qualifiers and source.
_CONTEXT = {
    "ecb-usd-liquidity-march-2020": (
        "In March 2020, the ECB and other central banks announced coordinated swap-line action to provide US dollar liquidity.",
        ("the ECB and other central banks", "central banks including the ECB"),
        ("announced a joint effort to make US dollars easier to obtain",
         "announced coordinated measures to improve access to US dollar funding"),
        "in March 2020",
    ),
    "eurostat-gdp-q2-2020": (
        "The September 2020 Eurostat release recorded a sharp quarter-on-quarter contraction of seasonally adjusted GDP in the second quarter during COVID-19 containment measures.",
        ("Eurostat",),
        ("reported a sharp quarterly decline in economic output during COVID-19 restrictions",
         "recorded a steep fall in quarterly GDP during pandemic restrictions"),
        "in the second quarter of 2020",
    ),
    "ecb-ukraine-march-2022": (
        "In March 2022, the ECB described how the Russian invasion of Ukraine raised energy costs, disrupted trade and weakened confidence.",
        ("the ECB", "the European Central Bank"),
        ("linked Russia's invasion of Ukraine to rising energy costs and disruptions to trade",
         "described higher energy costs and disrupted trade following Russia's invasion of Ukraine"),
        "in March 2022",
    ),
    "eurostat-hicp-october-2022": (
        "Energy made the largest contribution among the main components in this release.",
        ("Eurostat",),
        ("identified energy as the biggest contributor to annual inflation among the main categories",),
        "in its October 2022 inflation figures",
    ),
    "ecb-policy-july-2022": (
        "The ECB Governing Council announced a 50 basis point increase in its three policy rates on 21 July 2022.",
        ("the ECB", "the European Central Bank"),
        ("announced an increase in its three policy rates", "announced that it would raise all three policy rates"),
        "in July 2022",
    ),
    "ecb-policy-september-2023": (
        "In September 2023, the ECB announced a 25 basis point increase in its policy rates.",
        ("the ECB", "the European Central Bank"),
        ("announced an increase in its policy rates",
         "announced that it would raise its policy rates"),
        "in September 2023",
    ),
    "ecb-monetary-developments-2020-12": (
        "The January 2021 release recorded annual M3 growth of 12.3% in December 2020.",
        ("the ECB", "the European Central Bank"),
        ("reported that broad money was higher than a year earlier",
         "recorded year-on-year growth in the broad money supply"),
        "in its December 2020 figures",
    ),
}
_CAVEATS = (
    "The figures show what changed over the period, but they do not establish what caused the change.",
    "The data show how the indicator moved, but they cannot tell us how much of that move was due to the event.",
    "The timing provides useful context, but it does not show that the event caused the observed change.",
    "The evidence describes what happened, but it does not establish a cause.",
)
# Event-only wording is unavailable for questions without a selected or inferred event.
_GENERAL_CAVEATS = (_CAVEATS[0], _CAVEATS[-1])


@dataclass(frozen=True)
class Proposition:
    citation: str
    subjects: tuple[str, ...]
    predicates: tuple[str, ...]
    time_phrases: tuple[str, ...]
    prefixes: tuple[str, ...] = ()

    def prompt(self, evidence_id: int) -> dict:
        return {"evidence_id": evidence_id, "kind": self.citation.split(":")[0],
                "subjects": self.subjects, "predicates": self.predicates,
                "time_phrases": self.time_phrases, "optional_intro": self.prefixes}

    def accepts(self, text: str) -> bool:
        # The model chooses wording and placement within the grammar; it cannot
        # transfer numbers, units, direction, actors or qualifiers across IDs.
        for subject in self.subjects:
            for predicate in self.predicates:
                for time in self.time_phrases:
                    clause = f"{subject} {predicate}"
                    if text in (_sentence(f"{time}, {clause}"), _sentence(f"{clause} {time}")):
                        return True
                    if any(text == f"{intro}, {clause} {time}." for intro in self.prefixes):
                        return True
        return False


def _sentence(text: str) -> str:
    return text[0].upper() + text[1:] + "."


def movement(change: ObservedChange) -> Proposition:
    subject = _SUBJECTS[change.indicator_id]
    verbs = {
        "up": ("rose", "increased", "ended higher"),
        "down": ("fell", "declined", "ended lower"),
        "flat": ("ended unchanged", "was unchanged overall"),
    }[change.direction]
    # Avoid a contradictory "rose by 0.00%" for nonzero moves below display precision.
    magnitude = abs(Decimal(change.value))
    unit = ("%" if change.unit == "percent" else
            " percentage point" if magnitude == 1 else " percentage points")
    amounts = tuple(dict.fromkeys((f"{magnitude:.2f}", format(magnitude.normalize(), "f"))))
    numeric = tuple(f"{verb} by {amount}{unit}" for verb in verbs[:2] for amount in amounts) if magnitude else ()
    return Proposition(change.citation, subject, (*verbs, *numeric), _TIME)


def document_propositions(documents) -> list[Proposition]:
    propositions = []
    seen = set()
    for document in documents:
        source = document.metadata["id"]
        if source in seen or source not in _CONTEXT:
            continue
        original, subjects, predicates, time = _CONTEXT[source]
        if original in document.metadata["approved_sentences"] and original in document.page_content:
            propositions.append(Proposition(document.id, subjects, predicates, (time,), _TRANSITIONS))
            seen.add(source)
    return propositions


def permitted_caveats(event: bool, multiple: bool, correlation: Decimal | None = None) -> tuple[str, ...]:
    if multiple:
        if correlation is None:
            return ("There are too few comparable observations, or too little variation, to measure how closely "
                    "broad money growth and inflation moved together.",)
        coefficient = f"{correlation:.2f}"
        return (f"Broad money growth and inflation had a correlation of {coefficient} across matching months, "
                "which does not establish causation.",)
    return _CAVEATS if event and not multiple else _GENERAL_CAVEATS


def validate_narrative(output: object, propositions: list[Proposition], caveats: tuple[str, ...]) -> list[Claim]:
    draft = GeminiDraft.model_validate(output)
    required = {index for index, prop in enumerate(propositions, start=1) if prop.citation.startswith("series:")}
    series_ids = set()
    document_count = 0
    claims = []
    for sentence in draft.sentences:
        index = sentence.evidence_id
        if not 1 <= index <= len(propositions):
            raise ValueError("unknown evidence ID")
        proposition = propositions[index - 1]
        if not proposition.accepts(sentence.text):
            raise ValueError("wording does not match the cited proposition")
        if index in required:
            if index in series_ids or document_count:
                raise ValueError("series must be covered once before institutional context")
            series_ids.add(index)
        else:
            document_count += 1
        claims.append(Claim(text=sentence.text, citations=[proposition.citation]))
    if not required or series_ids != required or document_count != 1 or draft.caveat not in caveats:
        raise ValueError("missing required evidence or uncertainty")
    claims.append(Claim(text=draft.caveat, citations=[claim.citations[0] for claim in claims[:len(required)]]
                        if len(required) > 1 else [claim.citations[0] for claim in claims]))
    return claims


def fallback_narrative(changes: list[ObservedChange], evidence_ids: list[str]) -> list[Claim]:
    claims = []
    for change in changes:
        prop = movement(change)
        predicate = prop.predicates[3] if len(prop.predicates) > 3 else prop.predicates[0]
        claims.append(Claim(text=_sentence(f"{prop.subjects[0]} {predicate} {prop.time_phrases[0]}"),
                            citations=[change.citation]))
    if evidence_ids:
        text = (_GENERAL_CAVEATS[-1] if claims else
                "The official series does not support a reliable comparison between observations inside this window.")
        claims.append(Claim(text=text, citations=evidence_ids))
    return claims
