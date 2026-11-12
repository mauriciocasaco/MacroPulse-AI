"""Typed statistical and survey qualifiers for the controlled gate grammar."""
from __future__ import annotations

import re
from typing import Literal

Variant = Literal["headline", "constant_tax", "core", "nominal", "real", "net_of_tax", "gross_of_tax", "other"]
Statistic = Literal["point", "average", "maximum", "minimum", "change", "other"]
AverageMethod = Literal["calendar_day_weighted", "other"]
Subgroup = Literal["exporters", "large", "small", "all", "other"]
Denominator = Literal["survey_population", "all_population", "adopters"]
Purpose = Literal["personal", "work", "formal_education", "workflow_or_decision_support", "workflow", "decision_support", "other"]
Technology = Literal["any_ai", "generative_ai", "natural_language_generation"]

_VARIANTS: dict[Variant, str] = {
    "constant_tax": (
        r"constant[- ]tax(?:es| rates?)?|tax(?:es| rates?) (?:held|kept) (?:fixed|constant)|"
        r"(?:at|with) unchanged (?:indirect )?tax(?:es| rates?)|"
        r"(?:at|with) fixed (?:indirect )?tax(?:es| rates?)|hicp[- ]ct|ipca[- ]ic|"
        r"impuestos constantes|(?:tipos|tasas) impositiv[oa]s? (?:constantes|fij[oa]s?)|"
        r"(?:sin|exclud(?:e[ds]?|ing)|exclu(?:yendo|ye)) (?:los |the )?(?:cambios (?:de|en) impuestos|tax changes)"
    ),
    "net_of_tax": r"(?:net(?:to|a|o)? (?:of|de|after) |after[- ]|post[- ]|despues de (?:los )?)(?:tax(?:es|ation)?|impuestos?)|aftertax|tax[- ]net",
    "gross_of_tax": r"(?:gross (?:of|before) |before[- ]|pre[- ]|antes de (?:los )?)(?:tax(?:es|ation)?|impuestos?)|brut[oa] (?:de impuestos|sin deducciones)|pretax",
    "nominal": r"nominal(?:es)?",
    "real": r"real(?:es)?|inflation.adjusted|ajustad[oa] por inflacion",
    "core": r"core|subyacente|excluding (?:food|energy)|sin (?:alimentos|energia)",
    "headline": r"headline|(?:inflation|inflacion|hicp|ipca) (?:rate )?(?:general|overall|all.items)|all.items|inflacion general",
}
_STATISTICS: dict[Statistic, str] = {
    "average": r"averag\w*|mean|promedi\w*|medi[ao]s?|time.weighted|ponderad[oa] por tiempo",
    "maximum": r"maxim\w*|highest|peak|mas alt[oa]",
    "minimum": r"minim\w*|lowest|mas baj[oa]",
    "other": (
        r"median\w*|percentil\w*|quantil\w*|geometric mean|media geometrica|moving average|media movil|"
        r"(?:volume|transaction|balance)[- ]weighted|weighted by (?:volume|transactions?|balances?)|"
        r"ponderad[oa] por (?:volumen|transacciones|saldos)"
    ),
}
_PURPOSES: dict[Purpose, str] = {
    "personal": r"personal (?:purposes?|use)|(?:propositos?|fines?|uso) personal(?:es)?|personal(?:es)?",
    "work": r"work(?:[- ]related)?(?: purposes?)?|trabajo|laboral(?:es)?",
    "formal_education": r"formal education|educacion formal",
}
_SUBGROUPS: dict[Subgroup, str] = {
    "exporters": (
        r"export\w*|sell(?:ing|s)? (?:products? )?(?:overseas|abroad)|sold (?:goods )?(?:overseas|abroad)|"
        r"vend\w* (?:productos? )?al (?:exterior|extranjero)|ventas? (?:al exterior|internacionales)"
    ),
    "large": r"large|grandes?|(?:at least |al menos |con )?250 (?:or more |o mas )?(?:employees|empleados)",
    "small": r"small|pequen[oa]s?|medium.sized|medianas?|pymes?|smes?|micro\w*",
}


def statistical_variant(text: str) -> Variant | None:
    variants = [name for name, pattern in _VARIANTS.items() if re.search(rf"\b(?:{pattern})\b", text)]
    if "nominal" in variants and {"net_of_tax", "gross_of_tax"}.intersection(variants):
        variants.remove("nominal")
    if "headline" in variants and {"constant_tax", "core"}.intersection(variants):
        variants.remove("headline")
    if len(variants) > 1:
        return "other"
    if variants:
        return variants[0]
    if re.search(r"\b(?:tax\w*|impuest\w*|fiscalmente|vat|iva|levies|net|nett[oa]?|net[oa]|gross|brut[oa])\b", text):
        return "other"
    return None


def statistic(text: str) -> Statistic:
    found = [name for name, pattern in _STATISTICS.items() if re.search(rf"\b(?:{pattern})\b", text)]
    return found[0] if len(found) == 1 else "other" if found else "point"


def average_method(text: str) -> AverageMethod:
    daily_values = re.search(r"\b(?:daily (?:rates?|values?|observations?)|(?:tasas?|valores?|observaciones) diari[oa]s?)\b", text)
    explicit_weights = re.search(r"\b(?:weighted by|ponderad[oa] por) (?:the |los |las )?([a-z]+)", text)
    if explicit_weights and explicit_weights[1] not in {"days", "time", "calendar", "dias", "tiempo", "duracion"}:
        return "other"
    if re.search(
        r"\b(?:opening|closing|first|last|midpoint|two published|two observations|apertura|cierre|"
        r"primera|ultima|punto medio|dos observaciones|business days?|working days?|weekdays?|"
        r"excluding weekends|dias habiles|sin fines de semana)\b", text,
    ):
        return "other"
    if not daily_values and re.search(r"\b(?:arithmetic|unweighted|simple|equal.weighted|aritmetic[oa]|sin ponderar)\b", text):
        return "other"
    return "calendar_day_weighted"


def survey_purpose(text: str) -> Purpose | None:
    for name, pattern in _PURPOSES.items():
        if re.fullmatch(pattern, text.strip(" ,.;?")):
            return name
    return None


def subgroup(text: str) -> Subgroup | None:
    found = [name for name, pattern in _SUBGROUPS.items() if re.search(rf"\b(?:{pattern})\b", text)]
    return found[0] if len(found) == 1 else "other" if found else None
