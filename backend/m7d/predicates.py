"""Controlled predicates for the experimental Spanish/English evidence gate."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import re
from typing import Literal
import unicodedata

from backend.m7c.corpus import Geography, Scope
from scripts.m7b_schema import Claim, ClaimType, Indicator, Institution

from .question_constraints import Adjustment, Polarity, adjustment, counterfactual, exclusive, operands, polarity, relative_time
from .qualifiers import (
    AverageMethod, Denominator, Purpose, Statistic, Subgroup, Technology, Variant, average_method, statistic,
    statistical_variant, subgroup, survey_purpose,
)


Unit = Literal["percent", "percentage_points", "basis_points", "count", "level", "index_points"]
Population = Literal["enterprises", "enterprises_10_plus", "households", "persons", "persons_16_74"]
Operation = Literal[
    "observed_level", "level_change", "rate_difference", "temporal_average",
    "percentage_change", "percentage_point_difference", "rate_ratio",
]
Measure = Literal[
    "hicp_annual", "hicp_energy", "hicp_food", "hicp_energy_rate", "hicp_diagnosis",
    "gdp_quarterly", "gdp_consumption", "m3_annual", "m3_stock", "m1_annual", "m1_contribution",
    "deposit_decision", "deposit_effective", "deposit_in_force", "deposit_intention", "deposit_move", "app_schedule",
    "tltro_pricing", "ltro_pricing",
    "ai_enterprise", "ai_language", "ai_automation", "ai_household", "ai_purpose",
    "ai_tasks", "ai_hypothesis", "spot_move", "spot_level", "swap_liquidity", "series_change",
]


def normalize(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


_MONTHS = (
    "january|enero", "february|febrero", "march|marzo", "april|abril", "may|mayo", "june|junio",
    "july|julio", "august|agosto", "september|septiembre|setiembre", "october|octubre",
    "november|noviembre", "december|diciembre",
)
_MONTH_NUMBER = {name: month for month, names in enumerate(_MONTHS, 1) for name in names.split("|")}
_MONTH = "|".join(_MONTH_NUMBER)
_ORDINALS = {
    "first": 1, "primer": 1, "primero": 1, "second": 2, "segundo": 2,
    "third": 3, "tercer": 3, "tercero": 3, "fourth": 4, "cuarto": 4, "final": 4, "ultimo": 4,
}
_ORDINAL = "|".join(_ORDINALS)
_YEAR = r"(?:19|20)\d{2}"
_DAY = r"(?:[0-2]?\d|3[01])"
_DATES = re.compile(
    rf"(?<!\w)(?:(?P<iso>{_YEAR}-\d{{2}}-\d{{2}})"
    rf"|(?:(?P<qyear>{_YEAR})[ -]?)?[qt](?P<q>[1-4])(?: (?:of |de )?(?P<qy2>{_YEAR}))?"
    rf"|(?P<ordinal>{_ORDINAL})[- ](?:quarter|trimestre)(?: (?:of |de )?(?P<oy>{_YEAR}))?"
    rf"|(?:(?P<pre>{_DAY}) (?:de )?)?(?P<month>{_MONTH})(?: (?P<post>{_DAY})(?!\d))?"
    rf"(?:[ ,]+(?:de )?(?P<my>{_YEAR}))?"
    rf"|(?P<year>{_YEAR}))(?![\w-])"
)
_VINTAGE_BEFORE = (
    r"(?:release|publicaci[oó]n|publicad[oa]|published|released|comunicado|"
    r"estimaci[oó]n(?: flash)?|estimate|revisi[oó]n|revision|anunciad[oa]|announc(?:ed|ement)|bolet[ií]n)"
    r"(?: (?:in|on|of|en|del?|of the))?\s*$"
)
_VINTAGE_AFTER = r"^\s*(?:(?:flash|preliminary|final) )?(?:release|estimate|publication|announcement|comunicado)\b"
_AS_OF = r"(?:as of|disponible(?:s)? hasta(?: el)?|con corte(?: al)?|a fecha(?: de)?)\s*$"
_EFFECTIVE = r"(?:effective(?: from| on)?|to take effect (?:on|from)|vigente desde|en vigor(?: el| desde)?|aplicable desde)\s*$"


@dataclass(frozen=True)
class When:
    year: int | None
    month: int | None = None
    day: int | None = None
    quarter: int | None = None

    def bounds(self, fallback_year: int) -> tuple[date, date]:
        year = self.year or fallback_year
        if self.quarter:
            month = 3 * self.quarter - 2
            end_month = month + 2
        else:
            month = self.month or 1
            end_month = self.month or 12
        return date(year, month, self.day or 1), date(year, end_month, self.day or monthrange(year, end_month)[1])

    def includes_day(self, day: date) -> bool:
        try:
            start, end = self.bounds(day.year)
        except ValueError:
            return False
        return start <= day <= end


@dataclass(frozen=True)
class Times:
    reference: tuple[When, ...] = ()
    publication: tuple[When, ...] = ()
    effective: tuple[When, ...] = ()
    as_of: tuple[When, ...] = ()


def _time_mentions(text: str):
    for match in _DATES.finditer(text):
        g = match.groupdict()
        before, after = text[max(0, match.start() - 36):match.start()], text[match.end():]
        # Modal "may" and the verb "march" are also English month names.
        if g["month"] in {"may", "march"} and not any((g["my"], g["pre"], g["post"])):
            if not has(r"(?:in|on|of|en|de|last|since|during|from|to|after|before|the)\s*$", before) and not has(
                r"^\s*(?:release|estimate|meeting|flash|decision|publication|lockdown)\b", after
            ):
                continue
        if g["iso"]:
            year, month, day = map(int, g["iso"].split("-"))
            when = When(year, month, day)
        elif g["q"] or g["ordinal"]:
            year = g["qyear"] or g["qy2"] or g["oy"]
            when = When(int(year) if year else None, quarter=int(g["q"]) if g["q"] else _ORDINALS[g["ordinal"]])
        elif g["month"]:
            year = g["my"]
            day = g["pre"] or g["post"]
            when = When(int(year) if year else None, _MONTH_NUMBER[g["month"]], int(day) if day else None)
        else:
            when = When(int(g["year"]))
        if has(_AS_OF, before):
            role = "as_of"
        elif has(_EFFECTIVE, before):
            role = "effective"
        elif has(_VINTAGE_BEFORE, before) or has(_VINTAGE_AFTER, after):
            role = "publication"
        else:
            role = "reference"
        yield role, when, match.start(), match.end()


def times(text: str) -> Times:
    by_role: dict[str, list[When]] = {role: [] for role in ("reference", "publication", "effective", "as_of")}
    for role, when, _, _ in _time_mentions(text):
        by_role[role].append(when)
    return Times(**{role: tuple(dict.fromkeys(values)) for role, values in by_role.items()})


_ENERGY = r"\b(?:energy|energia|energetic[oa]s?)\b"
_FOOD = r"\b(?:food|alimentos?|alimentacion|tobacco|tabaco|alcohol)\b"
_CONTRIBUTION = r"\b(?:contribut\w*|contribu\w*|aport\w*|subtract\w*|rest\w*|added|add)\b"
_CONTRIBUTION_NOUN = r"\b(?:contributions?|contribucion(?:es)?|aportes?)\b"
_HICP = r"\b(?:hicp|ipca|inflation|inflacion|consumer prices|precios al consumidor)\b"
_DEPOSIT = r"\b(?:deposit(?: facility)?(?: rate)?|facilidad de deposito|tasa de deposito|tipo de deposito)\b"
_GDP = r"\b(?:gdp|pib|produccion trimestral|quarterly output|producto interno bruto|producto interior bruto)\b"
_M3 = r"\b(?:m3|broad money|masa monetaria amplia|agregado monetario amplio)\b"
_AI = r"\b(?:ai|ia|artificial intelligence|inteligencia artificial)\b"
_GENAI = r"\b(?:generative ai|genai|ia generativa|inteligencia artificial generativa)\b"
_FIRMS = r"\b(?:firms?|enterprises?|business(?:es)?|compan(?:y|ies)|empresas?|companias?|negocios?)\b"
_PEOPLE = r"\b(?:people|persons?|personas?|individuals?|individuos?|adults?|adultos?|residents?|residentes?|respondents?|poblacion encuestada)\b"
_HOUSEHOLDS = rf"(?:{_PEOPLE}|\b(?:households?|hogares?)\b)"
_LANGUAGE = r"\b(?:natural language generation|language generation|generacion (?:de )?(?:lenguaje(?: natural)?|texto))\b"
_WORKFLOW = r"\b(?:automat(?:ed|ing) workflows?|workflow automation|automatizacion (?:de )?(?:flujos|procesos)(?: de trabajo)?|flujos automatizados)\b"
_DECISION_SUPPORT = r"\b(?:decision support|apoyo (?:a |para )?(?:la toma de )?decisiones)\b"
_AUTOMATION = rf"(?:{_WORKFLOW}|{_DECISION_SUPPORT})"
_EXPECTATION = (
    r"\b(?:intend\w*|intent\w*|plan(?:ned|eaba|ific\w*)?|esper(?:ab\w*|ad\w*|an)|expect\w*|"
    r"prev(?:ist|ei)\w*|forecast\w*|proyect\w*|pronostic\w*|will|would use|projected)\b"
)
_REVISED = r"\b(?:revis(?:e[ds]?|ion|ed|o|aron|ada)|updated estimate|estimacion revisada)\b"
_CAUSAL_ACTOR = (
    r"(?:pandemic|war(?: in ukraine)?|(?:russian )?invasion|covid(?:-19)?(?: pandemic| lockdowns?)?|generative ai|genai)"
)
_CAUSAL = (
    r"\b(?:caus\w*|attribut\w*|atribu\w*|causal|caused by|due to|because of|debido a|"
    r"provoc\w*|produjo|genero|impuls[oó]|explic[oó]|cuanto del .* se debio|counterfactual|contrafactual|"
    rf"(?:did|does) (?:the )?{_CAUSAL_ACTOR} "
    r"(?:move|affect|increase|raise|lower|reduce|change|produce|drive|shape|fuel|boost|depress|explain|push|dampen)|"
    r"(?:driven|fuelled|fueled|boosted|depressed|shaped|triggered|pushed|explained) by|"
    r"(?:effect|impact) (?:did|of|on)|impacto de|efecto de|did .*? produce|why (?:did|was|were)|por que|"
    r"por (?:la (?:pandemia|invasion|guerra|ia generativa)|(?:el )?covid(?:-19)?))\b"
)
_ATTRIBUTED = r"\b(?:what (?:did .*? (?:say|cite|identify|attribute)|explanation)|que (?:dijo|cito|atribuyo|explico)|que explicacion)\b"
_REPORTING = r"\b(?:according to|segun|what did .*? (?:say|cite|identify|attribute)|que (?:dijo|cito|atribuyo|explico))\b"
_INSTITUTIONS = {
    "BCE": r"\b(?:ecb|bce|european central bank|banco central europeo)\b",
    "Eurostat": r"\beurostat\b",
    "Reserva Federal (Junta)": r"\b(?:fed|federal reserve|reserva federal)\b",
}
_PERCENT_POINTS = r"\b(?:percentage[- ]points?|puntos porcentuales?|pp)\b"
_BASIS_POINTS = r"\b(?:basis points?|puntos basicos?|bps|pb)\b"
_PERCENT = r"%|\b(?:percent(?:age)?|por ciento|porcentaj\w*)\b"
_COUNT = (
    r"\b(?:number of|absolute (?:count|number)|whole numbers?|headcount|total count|"
    r"(?:numero|cantidad) (?:absolut[oa] )?de|(?:numero|cifra) enter[oa]|en numeros absolutos)\b"
)
_HOW_MANY = r"\b(?:how many|cuant[oa]s)\b"
_MOVE = r"\b(?:change[ds]?|mov(?:e[ds]?|ement)|vari(?:o|acion)|cambi\w*|swing|balance)\b"
_GEO = {
    "euro-area": r"\b(?:euro[- ]area|euro zone|eurozone|zona euro|eurozona|ea(?:19|20|21))\b",
    "eu": r"\b(?:eu(?:27)?|ue(?:27)?|european union|union europea)\b",
    "us": r"\b(?:u\.?s\.?a?\.?|united states|estados unidos|ee\.?\s?uu\.?)\b",
}
_OTHER_GEO = (
    r"\b(?:german(?:y)?|alemania|france|french|francia|spain|spanish|espana|espanol\w*|italy|italian|italia|canada|"
    r"mexic[oa]|japan|japon|china|united kingdom|reino unido|uruguay|netherlands|paises bajos|"
    r"poland|polonia|finland|finlandia|australia|brazil|brasil|ireland|irlanda|uk|argentina)\b"
)
_EVENTS = {
    "covid": r"\b(?:covid(?:[- ]?19)?|pandemic|pandemia|coronavirus|lockdowns?|confinamientos?)\b",
    "ukraine": r"\b(?:ukraine|ucrania|invasion|(?:russian|rusa) (?:invasion|war|guerra)|(?:war|guerra) (?:in|en|de) (?:ukraine|ucrania))\b",
    "genai": _GENAI,
}
_SUBPOPULATION = (
    r"\b(?:small (?:firms|enterprises|businesses)|pymes|microempresas|manufactur\w*|"
    r"retail(?:ers)?|startups?|women|mujeres|men|hombres|children|ninos|"
    r"(?:younger|older) (?:adults|people)|under \d+|over \d+|aged? (?!16\b)\d+|"
    r"bajo \d+|mayores de \d+|menores de \d+)\b"
)
_NARROW_PRICE = (
    r"\b(?:concerts?|conciertos?|tickets?|entradas?|cinema|cine|airfares?|flights?|vuelos?|"
    r"restaurants?|restaurantes?|rent(?:s|al)?|alquiler(?:es)?|gasoline|gasolina|diesel|"
    r"electricity|electricidad|gas prices?|precio del gas|bread|pan|eggs|huevos|housing|vivienda|"
    r"hotels?|hoteles?|clothing|ropa|shoes|calzado|petrol|transport|transporte|medical|"
    r"core inflation|inflacion subyacente|excluding|excluyendo)\b"
)
_BASKET_EXCLUSION = r"\b(?:excluding|excluyendo|without energy|sin energia|net of|net[oa] de|excluding taxes|tax adjusted)\b"
_NON_ANNUAL_HICP = (
    r"\b(?:monthly|quarterly|month[- ](?:on|over)[- ]month|quarter[- ](?:on|over)[- ]quarter|"
    r"mensual|intermensual|trimestral|intertrimestral|mom|qoq|m/m|q/q)\b"
)
_HICP_COMPARISON = (
    r"\b(?:exceed\w*|(?:higher|lower|more|less) than|super[oa]|(?:mayor|menor) que|"
    r"difference|gap|delta|diferencia|brecha)\b|"
    r"\b(?:increas\w*|decreas\w*|ris[er]\w*|rose|fell|fall|chang\w*|go (?:up|down)|"
    r"subi[oó]|aument\w*|"
    r"baj\w*|cay[oó]|vari\w*)\b[^?;.]*"
    r"\b(?:versus|vs|relative to|compared (?:to|with)|respecto (?:a|al|de|del)|en comparacion con|"
    r"over (?:the )?(?:previous|preceding|prior|last) (?:month|quarter|year))\b|"
    r"\b(?:higher|lower|more|less|mayor|menor|mas|menos)\b[^?;.]*\b(?:than|que)\b|"
    r"\b(?:change|increase|decrease|rise|fall|cambio|variacion|aumento|caida)\b[^?;.]*"
    r"\b(?:from|between|desde|entre|compared (?:to|with)|respecto (?:a|al|de|del))\b"
)
_PREVIOUS_MONTH = r"\b(?:(?:previous|preceding|prior|last) month|mes (?:anterior|previo)|mes pasado)\b"
_RATE_TARGET = r"\b(?:inflation|inflacion|rate|tasa|annual|interanual|year[- ](?:on|over)[- ]year)\b"
_RATE_RATIO = (
    r"\b(?:how many times|times as (?:high|large|low)|ratio (?:of|between)|multiple of|"
    r"(?:by )?what factor|factor by which|multiplicative factor|fold (?:change|increase|decrease)|"
    r"cuantas veces|por que factor|factor multiplicativo|cociente (?:de|entre)|razon entre)\b"
)
_COMPARISON_PROMPT = (
    r"by (?:how much|what (?:margin|amount)|how many (?:percentage[- ]points?|points?))|"
    r"en cuant(?:o|os puntos(?: porcentuales)?)|por que margen"
)
_COMPARISON_ELLIPSIS = re.compile(
    rf"^(?:(?:{_COMPARISON_PROMPT}) (?:did it (?:exceed|differ from)|was it (?:higher|lower) than|"
    r"supero|fue (?:mayor|menor) que)|"
    r"(?:its |the )(?:change|difference) (?:from|versus|relative to)|"
    r"(?:su |la )(?:variacion|diferencia) (?:respecto (?:a|al|de|del)|frente a))\b"
)
_ANNOUNCED = r"\b(?:announc\w*|decid\w*|decision|anunci\w*|decisio\w*|reuni[oó]n|meeting)\b"
_EFFECTIVE_DATE_QUESTION = r"\b(?:when|what date|which date|from what date|desde que fecha|cuando|en que fecha|a partir de que fecha)\b"
_IN_FORCE = (
    r"\b(?:in force|in effect|in place|actual(?:ly)?|prevailing|then[- ]current|aplicad[oa]|"
    r"aplicaba|regia|rigio|en vigor|vigente|efectiv[oa])\b"
)
_ACTUAL_POLICY = r"\b(?:actually|in fact|de hecho|efectivamente|realmente)\b"
_PROJECTED_POLICY = r"\b(?:would|will|was to) be\b|\b(?:estaria|estara|regiria|regira|entraria|entrara)\b"
_ADOPTION_MEASURES = frozenset({"ai_enterprise", "ai_language", "ai_automation", "ai_household", "ai_purpose"})
_ADOPTION_VERB = r"\b(?:used?|using|adopt(?:ed|ing)?|usa(?:ban|ron|ba|r)?|uso|utiliz(?:aba|aban|aron|o|ar))\b"
_FIRM_THRESHOLD = (
    r"\b(?:(?:ten|diez|10) (?:or more|o mas) (?:employees|empleados)|"
    r"(?:ten|diez|10) (?:employees|empleados) (?:or more|o mas)|"
    r"(?:at least|al menos) (?:ten|diez|10) (?:employees|empleados))\b"
)
_AGE_THRESHOLD = r"\b16\s*(?:to|a|[-–—]|and|y)\s*74\b"
_AGGREGATE_GDP_PREFIX = {
    *_MONTH_NUMBER, *_ORDINALS, "actual", "aggregate", "area", "eurozone", "euro", "eu", "ue", "ea19", "ea20",
    "ea21", "gdp", "quarterly", "quarter", "on", "the", "was", "did", "does", "how", "what", "of", "and", "much",
    "estimate", "estimated", "preliminary", "flash", "reported", "report", "revise", "revised", "say", "cause",
    "caused", "because",
    "de", "del", "el", "la", "su", "un", "al", "y", "del", "cayo", "crecio", "subio", "bajo",
}
_GEO_OR_PERIOD_START = {
    *_MONTH_NUMBER, *_ORDINALS, "euro", "eurozone", "eurozona", "zona", "ea19", "ea20", "ea21",
    "eu", "ue", "union", "european", "united", "us", "estados", "quarter", "trimestre", "q1", "q2", "q3", "q4",
    "both", "ambos", "t1", "t2", "t3", "t4",
}


@dataclass(frozen=True)
class Requirement:
    measure: Measure
    timing: Times
    unit: Unit | None = None
    revised: bool = False
    flash: bool = False
    institution: Institution | None = None
    premise: Decimal | None = None
    modality: ClaimType | None = None
    population: Population | None = None
    area_definition: str | None = None
    polarity: Polarity = "affirmative"
    adjustment: Adjustment | None = None
    variant: Variant | None = None
    statistic: Statistic = "point"
    subgroup: Subgroup | None = None
    purpose: Purpose | None = None
    technology: Technology | None = None
    indicator: Indicator | None = None
    geography: Geography | None = None
    description: str = ""
    whole_period: bool = False
    announced: bool = False
    denominator: Denominator | None = None
    average_method: AverageMethod | None = None
    operation: Operation | None = None
    comparison: RateComparison | None = None


@dataclass(frozen=True)
class RateComparison:
    base: When
    target: When
    direction: Literal["higher", "lower"] | None = None
    absolute: bool = False


@dataclass(frozen=True)
class Intent:
    requirements: tuple[Requirement, ...]
    causal: bool
    unsupported: bool = False
    whole_period: bool = False
    unknown_parts: bool = False
    unmatched_parts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RateCoverage:
    start: date
    end: date
    value: Decimal


@dataclass(frozen=True)
class Predicate:
    measure: Measure
    units: tuple[Unit, ...] = ()
    timing: Times = Times()
    previous_reference: bool = False
    flash: bool = False
    population: Population | None = None
    polarity: Polarity = "affirmative"
    adjustment: Adjustment | None = None
    variant: Variant | None = None
    statistic: Statistic = "point"
    subgroup: Subgroup | None = None
    purpose: Purpose | None = None
    technology: Technology | None = None
    rate_coverage: RateCoverage | None = None
    decision_date: date | None = None
    revised: bool = False
    effective_date: date | None = None
    denominator: Denominator | None = None
    average_method: AverageMethod | None = None


def units(text: str, *, adoption: bool = False) -> tuple[Unit, ...]:
    found: list[Unit] = []
    if has(_PERCENT_POINTS, text):
        found.append("percentage_points")
        text = re.sub(_PERCENT_POINTS, "", text)
    if has(_BASIS_POINTS, text):
        found.append("basis_points")
    if has(_PERCENT, text):
        found.append("percent")
    if has(r"\b(?:millions? of (?:euros|eur)|millones de euros|usd per eur|dolares por euro)\b", text):
        found.append("level")
    if has(r"\b(?:index points?|puntos (?:del |de )?indice|index level|nivel del indice)\b", text):
        found.append("index_points")
    if has(_COUNT, text) or (adoption and not found and has(_HOW_MANY, text)):
        found.append("count")
    if adoption and not found and has(r"\b(?:share|fraction|proportion|proporcion|fraccion)\b", text):
        found.append("percent")
    return tuple(found)


def _ai_measures(text: str) -> tuple[Measure, ...]:
    if has(r"\b(?:tasks?|tareas?)\b", text) and has(r"\b(?:productiv\w*|gains?|rendimiento)\b", text):
        return ("ai_tasks",)
    if has(_HOUSEHOLDS, text):
        return ("ai_purpose",) if has(r"\b(?:purposes?|propositos?|personal|trabajo|work|educacion|education)\b", text) else ("ai_household",)
    if has(_LANGUAGE, text):
        return ("ai_language",)
    if has(_AUTOMATION, text):
        return ("ai_automation",)
    if has(_FIRMS, text):
        return () if has(_GENAI, text) else ("ai_enterprise",)
    if has(r"\b(?:pace|ritmo|extent|magnitud|economic (?:effects?|impact)|efectos economicos)\b", text) and has(
        r"\b(?:choices|decisions|decisiones|depend\w*)\b", text,
    ):
        return ("ai_hypothesis",)
    return ()


def _adoption_population(text: str) -> Population | None:
    subject = re.search(rf"{_FIRMS}|{_PEOPLE}|\b(?:households|hogares?)\b", text)
    if not subject:
        return None
    if has(_FIRMS, subject.group()):
        return "enterprises_10_plus" if has(_FIRM_THRESHOLD, text) else "enterprises"
    if has(_PEOPLE, subject.group()):
        return "persons_16_74" if has(_AGE_THRESHOLD, text) else "persons"
    return "households"


def _enterprise_qualifier(text: str) -> bool:
    if has(rf"\b(?:all|todas las) (?:eu |ue |european union )?{_FIRMS}", text):
        return True
    prefix = re.search(rf"\b([a-z]+) (?:(?:eu|ue|european union) )?{_FIRMS}", text)
    if prefix and prefix.group(1) not in {
        "eu", "ue", "of", "the", "many", "among", "for", "by", "de", "las", "los",
        "cuantas", "cuantos", "entre", "en", "por", "que", "which", "what", "did", "does", "las", "para",
    }:
        return True
    suffix = re.search(rf"{_FIRMS} ([a-z]+)\b", text)
    if suffix and suffix.group(1) not in {
        "in", "of", "from", "with", "en", "de", "del", "con", "used", "using", "use", "adopted",
        "adopt", "that", "which", "were", "was", "did", "had", "have", "will", "would", "report",
        "reported", "never", "no", "nunca", "utilizaban", "utilizaba", "utilizaron", "uso", "usaban", "usaron", "que",
    }:
        return True
    locative = re.search(rf"{_FIRMS} (?:in|within|across|of|en|de|del) (?:the |la |el )?([a-z0-9]+)", text)
    return bool(locative and locative.group(1) not in _GEO_OR_PERIOD_START and not locative.group(1).isdecimal())


def _adoption_subgroup(text: str, population: Population | None) -> Subgroup | None:
    named = subgroup(text)
    if named:
        return named
    if has(_SUBPOPULATION, text) or has(r"\b(?:by industry|por sector|by member state)\b", text):
        return "other"
    if population in {"enterprises", "enterprises_10_plus"}:
        if has(rf"\b(?:all|todas las) (?:eu |ue |european union )?{_FIRMS}", text):
            return "all"
        if _enterprise_qualifier(text) or has(
            rf"{_FIRMS} (?:that|which|que) (?!{_ADOPTION_VERB})|{_FIRMS} (?:with|con) (?!{_FIRM_THRESHOLD})",
            text,
        ) or (has(r"\b(?:employees|empleados)\b", text) and not has(_FIRM_THRESHOLD, text)):
            return "other"
    if population in {"persons", "persons_16_74", "households"} and (
        (has(r"\b(?:aged?|ages?|edad(?:es)?|years old|anos de edad)\b", text) and not has(_AGE_THRESHOLD, text))
        or has(r"\b(?:adults?|adultos?|adultas?)\b", text)
        or has(rf"{_HOUSEHOLDS} (?:with|con|who|that|que) (?!{_ADOPTION_VERB})", text)
        or _person_qualifier(text)
    ):
        return "other"
    return None


def _person_qualifier(text: str) -> bool:
    geographies = "|".join(_GEO.values())
    unlocated = re.sub(geographies, "", text)
    unlocated = normalize(re.sub(r"(?<=\w)['’]s\b", "", unlocated))
    prefix = re.search(rf"\b([a-z]+) {_PEOPLE}", unlocated)
    if prefix and prefix[1] not in {
        "the", "of", "among", "from", "for", "by", "did", "surveyed", "sampled",
        "de", "del", "las", "los", "la", "el", "por", "entre", "para", "que",
    }:
        return True
    location = re.search(rf"{_PEOPLE} (?:in|within|en) (?:the |la |el )?([a-z0-9]+)\b", text)
    if location and location[1] not in _GEO_OR_PERIOD_START and not location[1].isdecimal():
        return True
    suffix = re.search(rf"{_PEOPLE} ([a-z]+)\b", unlocated)
    return bool(suffix and suffix[1] not in {
        "used", "use", "using", "adopted", "adopting", "did", "report", "reported", "surveyed",
        "in", "on", "of", "from", "were", "who", "that", "aged", "ages",
        "usaron", "usa", "uso", "usaba", "utilizaron", "utilizo", "utilizaba", "declaro", "declararon",
        "encuestadas", "encuestados", "de", "del", "en", "que", "con",
    })


def _adoption_denominator(text: str) -> Denominator:
    if has(
        rf"\b(?:among|of|entre|de) (?:the |los |las )?"
        rf"(?:ai users?|usuarios? de ia|{_FIRMS} (?:already |ya )?(?:using|adopting|usuari[oa]s? de) {_AI})",
        text,
    ):
        return "adopters"
    if has(rf"\b(?:all|todas las|todos los) (?:eu |ue |european union )?(?:{_FIRMS}|{_HOUSEHOLDS})", text):
        return "all_population"
    return "survey_population"


def _automation_purpose(text: str) -> Purpose | None:
    if re.fullmatch(rf"(?:{_WORKFLOW} (?:or|o) {_DECISION_SUPPORT}|{_DECISION_SUPPORT} (?:or|o) {_WORKFLOW})", text):
        return "workflow_or_decision_support"
    if re.fullmatch(_WORKFLOW, text):
        return "workflow"
    if re.fullmatch(_DECISION_SUPPORT, text):
        return "decision_support"
    return None


def _technology_object(text: str, population: Population | None) -> tuple[Measure, Purpose | None] | None:
    text = text.strip(" ,.;?").removeprefix("the ").removeprefix("la ").removeprefix("las ")
    if re.fullmatch(_LANGUAGE, text):
        return "ai_language", None
    activity = _automation_purpose(re.sub(rf"^{_AI} (?:for|para) ", "", text))
    if activity:
        return "ai_automation", activity
    personal = re.fullmatch(
        rf"{_GENAI}(?: (?:tools?|herramientas?))?(?: (?:for|para|con (?:fines?|propositos?) (?:de )?)(.+))?", text,
    )
    if population in {"persons", "persons_16_74", "households"} and personal:
        if personal[1] is None:
            return "ai_household", None
        purpose = survey_purpose(personal[1])
        return ("ai_purpose", purpose) if purpose else None
    if population in {"enterprises", "enterprises_10_plus"} and re.fullmatch(
        r"(?:(?:any|some|at least one|alguna) )?"
        r"(?:(?:technolog(?:y|ies)|tecnologias?) (?:de )?)?"
        r"(?:ai|ia|artificial intelligence|inteligencia artificial)"
        r"(?: (?:technolog(?:y|ies)|tools?|herramientas?))?",
        text,
    ):
        return "ai_enterprise", None
    return None


def _adoption_tail(text: str) -> str | None:
    match = re.search(rf"{_ADOPTION_VERB} +(.+)", text)
    if not match:
        return None
    tail = re.split(r"[?.;]|\b(?:according to|segun)\b", match.group(1), maxsplit=1)[0].strip()
    if has(r"^(?:among|entre|by|por parte de|in|en|during|durante)\b", tail) and has(_AI, text[:match.start()]):
        # In a noun phrase like “generative AI use among people”, the
        # technology precedes the word “use”.
        return None
    removals = []
    for _, _, start, end in _time_mentions(tail):
        connector = re.search(r"\b(?:in|during|en|durante|as of|a fecha de) (?:the |el )?$", tail[:start])
        if connector:
            removals.append((connector.start(), end))
    for start, end in reversed(removals):
        tail = f"{tail[:start]} {tail[end:]}"
    return normalize(tail)


def _adoption_objects(text: str) -> tuple[tuple[tuple[Measure, Purpose | None], ...], tuple[str, ...]]:
    """Recognize enumerated technology operands without inheriting the first operand."""
    front = re.match(
        r"^(?:for|para|in|en|con fines? de) (.+?)(?:,|\s+(?=(?:what|which|how|que|cual|cuant[oa]s?)\b))",
        text,
    )
    if front:
        remainder = _DATES.sub("", front[1])
        for pattern in (*_GEO.values(), _FIRMS, _HOUSEHOLDS, _FIRM_THRESHOLD, _AGE_THRESHOLD):
            remainder = re.sub(pattern, "", remainder)
        remainder = re.sub(r"\b(?:the|of|in|during|la|el|las|los|de|en|durante|with|con)\b", "", remainder)
        if remainder.strip():
            # A leading activity can restrict an otherwise generic adoption clause.
            return (), (text,)
    tail = _adoption_tail(text)
    if not tail:
        # Survey nominal forms still need an exact technology object and activity.
        nominal = re.search(rf"(?:{_GENAI}|{_AI})(?: (?:tools?|herramientas?))? (?=(?:use|usage|adoption|uso|adopcion)\b)", text)
        if not nominal:
            return (), (text,)
        tail = nominal.group().strip()
        if has(r"\b(?:for|para|fines|purpose|proposito)\b", text[nominal.end():]):
            return (), (text,)
    individual = has(r"\b(?:respectively|separately|each|por separado|respectivamente|cada)\b", text)
    if not individual and has(
        r"\b(?:both|jointly|together|simultaneously|at the same time|amb[oa]s|en conjunto|a la vez|simultaneamente)\b"
        r"|\btanto\b.+\bcomo\b", tail,
    ):
        return (), (tail,)
    tail = re.sub(r"\b(?:respectively|separately|por separado|respectivamente)\b", "", tail).strip(" ,")
    population = _adoption_population(text)
    single = _technology_object(tail, population)
    if single:
        return (single,), ()
    shared_purpose = re.fullmatch(
        rf"(?P<technology>{_GENAI}(?: (?:tools?|herramientas?))?) (?:for|para|con (?:fines?|propositos?) (?:de )?)(?P<purposes>.+)", tail,
    )
    if shared_purpose:
        objects = operands(shared_purpose["purposes"])
        recognized = tuple(("ai_purpose", purpose) if (purpose := survey_purpose(item)) else None for item in objects)
    else:
        objects = operands(tail)
        recognized = tuple(_technology_object(item, population) for item in objects)
    unknown = tuple(obj for obj, known in zip(objects, recognized, strict=True) if known is None)
    if len(objects) > 1 and not individual:
        return (), (tail,)
    return tuple(dict.fromkeys(item for item in recognized if item)), unknown


def _adoption_question(text: str) -> bool:
    return has(r"\b(?:us(?:e[ds]?|ing|o|a[bdn]\w*|ar(?:on)?)|utiliz\w*|adopt\w*|adopc\w*)\b", text)


def _aggregate_hicp(text: str) -> bool:
    # A specific sector/product after the rate noun is a different measurement.
    # Allow only known kinds of modifiers in that syntactic position.
    narrowed = re.search(rf"{_HICP} (?:rate |tasa )?(?:for|of|among|in|de|del|en|entre|para) (?:the |la |el |las |los )?(\w+)", text)
    if narrowed and narrowed.group(1) not in {
        *_MONTH_NUMBER, "euro", "eurozona", "zona", "ea19", "ea20", "eu", "ue", "union",
        "2020", "2022", "2023", "q1", "q2", "q3", "q4", "lockdown", "confinamiento", "impuestos", "taxes",
    } and not narrowed.group(1).isdecimal():
        return False
    if has(r"\b(?:across|por ciudades|entre ciudades)\b", text):
        return False
    if has(r"\b(?:price|precios?) (?:of|for|de|del|para)\b", text):
        return False
    modifier = re.search(r"\b([a-z]+) (?:inflation|inflacion)\b", text)
    return not modifier or modifier.group(1) in {
        "annual", "year", "headline", "aggregate", "total", "overall", "general", "euro", "area", "eurozone",
        "hicp", "ipca", "ea19", "ea20", "ea21", "was", "did", "is", "what", "how", "the", "reported",
        "annualized", "de", "la", "en", "para", "in", "rate", "tasa", "of", "to", "core", "subyacente",
        "average", "mean", "highest", "lowest", "maxima", "minima", "maximum", "minimum", "tax",
    }


def _supported_subject(text: str, measures: tuple[Measure, ...]) -> bool:
    if "hicp_annual" in measures and not _aggregate_hicp(text):
        return False
    if "gdp_quarterly" in measures:
        # The bare aggregate has a closed set of grammatical modifiers. A
        # geographic adjective in this position is a different target series.
        subject = re.sub(r"(?<=\w)['’]s\b", "", text)
        prefix = re.search(
            r"\b([a-z0-9]+)[ -]+(?:(?:quarter-on-quarter|quarterly|real|raw|unadjusted|"
            r"(?:(?:not )?(?:seasonally|calendar)[- ])?adjusted) )*gdp\b", subject,
        )
        if prefix and prefix.group(1) not in _AGGREGATE_GDP_PREFIX and not re.fullmatch(_YEAR, prefix.group(1)):
            return False
        locative = re.search(
            r"\b(?:gdp|pib)(?: (?:real|growth rates?|growth|change|contraction|decline|drop|estimate|"
            r"crecimiento|caida|contraccion|trimestral)){0,3}"
            r" (?:in|for|of|en|de|del|para) (?:the |la |el )?([a-z0-9]+)\b", text,
        )
        if locative and locative.group(1) not in _GEO_OR_PERIOD_START and not locative.group(1).isdecimal():
            return False
        modified = re.search(
            r"\b(?:gdp|pib)(?: real)? ([a-z]+)\b", text
        )
        if modified and modified.group(1) not in {
            *_MONTH_NUMBER, "real", "growth", "contraction", "change", "rate", "output", "estimate",
            "estimates", "figures", "estimate", "fall", "drop", "decline", "level", "grew", "rose",
            "fall", "fell", "contracted", "did", "was", "were", "in", "for", "of", "on", "at", "by",
            "to", "from", "and", "after", "before", "during", "since", "cause", "caused", "move",
            "subio", "cayo", "crecio", "estimo", "registro", "reporto", "publico", "trimestral", "cambio", "real", "nominal",
            "del", "de", "en", "por", "se", "el", "durante", "y", "tras", "despues", "segun", "fue",
            "before", "prior", "unadjusted", "raw", "seasonally", "calendar", "sin", "antes", "bruto",
        }:
            return False
    if "deposit_decision" in measures:
        decision = has(
            r"\b(?:decid\w*|decision|decisi[oó]n|set|kept|held|hold|left|maintain\w*|"
            r"mant(?:uvo|en\w*)|fix\w*|fij\w*|rais\w*|lower\w*|subi[oó]|redujo|recort\w*|"
            r"cut|rate level|nivel|what (?:was|is) (?:the )?|cual (?:era|fue|es) (?:la |el )?)\b", text
        )
        if not decision and not has(_ANNOUNCED, text):
            return False
    if _ADOPTION_MEASURES.intersection(measures):
        if not _adoption_question(text):
            return False
    return True


def _hicp_measures(text: str) -> tuple[Measure, ...]:
    energy, food = has(_ENERGY, text), has(_FOOD, text)
    if (energy or food) and has(_CONTRIBUTION, text):
        selected: list[Measure] = []
        if has(r"\b(?:headline|general|total|aggregate|agregad[oa]|annual inflation rate|tasa interanual)\b", text):
            if has(r"\b(?:and what|y (?:cuanto|que|cual)|as well as|asi como)\b", text):
                selected.append("hicp_annual")
        if energy:
            selected.append("hicp_energy")
        if food:
            selected.append("hicp_food")
        return tuple(selected)
    if has(_REPORTING, text) and has(r"\b(?:drivers?|presion\w*|factors?|causes?|motivos?|factor(?:es)?)\b", text):
        return ("hicp_diagnosis",)
    if has(_CONTRIBUTION, text):
        return ()
    if energy:
        return ("hicp_energy_rate",)
    if food or not has(_HICP, text):
        return ()
    return ("hicp_annual",)


def _contribution_actors(text: str) -> str | None:
    nominal = re.search(rf"{_CONTRIBUTION_NOUN} (?:of|from|de|del) (.+)", text)
    if nominal:
        return re.split(r"\b(?:to|in|a la|al|en)\b|[?.;]", nominal.group(1), maxsplit=1)[0]
    verbal = re.search(_CONTRIBUTION, text)
    if not verbal:
        return None
    before = text[:verbal.start()]
    actors = re.split(
        r"\b(?:did|does|do|give|report|provide|(?:what|how large|how big) (?:is|was|are|were)|"
        r"cual fue|cuant[oa]|que|como)\b",
        before,
    )[-1]
    actors = re.sub(r"(?:['’]s)? (?:accounting|contable)$|['’]s$", "", actors.strip(" ¿?"))
    if has(_ENERGY, actors) or has(_FOOD, actors):
        return actors
    if has(r"\b(?:aport|rest|contribu)", verbal.group()):
        after = re.split(r"\b(?:a la|al|en)\b|[?.;]", text[verbal.end():], maxsplit=1)[0]
        if has(_ENERGY, after) or has(_FOOD, after):
            return after
    return None


def _contribution_objects(text: str) -> tuple[tuple[Measure, ...], bool] | None:
    actors = _contribution_actors(text)
    if not actors:
        return None
    # The publisher's combined food category is one accounting component.
    actors = re.sub(r"\bfood,? alcohol and tobacco\b|\balimentos,? alcohol y tabaco\b", "food", actors)
    actors = re.sub(r"\b(?:separately|respectively|each|por separado|respectivamente|cada uno)\b", "", actors)
    objects = tuple(item.removeprefix("the ").removeprefix("la ").removeprefix("el ") for item in operands(actors))
    measures: list[Measure] = []
    for item in objects:
        if re.fullmatch(_ENERGY, item):
            measures.append("hicp_energy")
        elif re.fullmatch(_FOOD, item):
            measures.append("hicp_food")
    separate = has(r"\b(?:separately|respectively|each|por separado|respectivamente|cada uno)\b", text)
    comparison = has(r"\b(?:more than|less than|mas que|menos que)\b", text)
    if comparison and len(objects) == len(measures) == 1:
        return None
    additional = has(rf"\b(?:and|plus|y|mas) [a-z ]{{1,80}}{_CONTRIBUTION_NOUN}", text)
    unknown = len(objects) != len(measures) or (len(objects) > 1 and not separate) or additional
    return tuple(dict.fromkeys(measures)), unknown


def _policy_measures(text: str) -> tuple[Measure, ...]:
    if has(r"\btltros?\b", text) and has(r"\b(?:pricing|priced|terms|rate|tasa|condiciones)\b", text):
        return ("tltro_pricing",)
    if has(r"\bltros?\b", text) and has(r"\b(?:pricing|priced|terms|rate|tasa|condiciones)\b", text):
        return ("ltro_pricing",)
    if has(r"\b(?:swaps?|tltro|ltro|refinancing|refinanciacion|marginal lending|facilidad marginal|pepp|total euros|total interest)\b", text):
        return ()
    if has(r"\b(?:app|asset purchase(?:s| programme)?|compras de activos)\b", text):
        return ("app_schedule",)
    if has(_EXPECTATION, text) and (has(_DEPOSIT, text) or has(r"\b(?:rates?|tasas?|increase|subida)\b", text)):
        return ("deposit_intention",)
    if not has(_DEPOSIT, text):
        return ()
    if has(_EFFECTIVE_DATE_QUESTION, text) and has(
        rf"{_IN_FORCE}|\b(?:effective|effect|aplicable|aplico|vigencia)\b", text,
    ):
        return ("deposit_effective",)
    if statistic(text) != "point":
        if has(r"\b(?:of (?:the )?(?:announcements?|decisions?)|de (?:los |las )?(?:anuncios?|decisiones))\b", text):
            return ()
        return ("deposit_in_force",)
    if has(_IN_FORCE, text) and not (has(_ANNOUNCED, text) and has(_PROJECTED_POLICY, text)):
        return ("deposit_in_force",)
    if has(_MOVE, text) or has(_BASIS_POINTS, text) or has(_PERCENT_POINTS, text):
        return ("deposit_move",)
    if has(_ANNOUNCED, text) or has(r"\b(?:kept|left|maintain\w*|mantuvo|dejo|fijo|set)\b", text):
        return ("deposit_decision",)
    return ("deposit_in_force",)


def _money_measures(text: str) -> tuple[Measure, ...]:
    if has(r"\bm1\b", text):
        return ("m1_contribution",) if has(_CONTRIBUTION, text) else ("m1_annual",)
    if not has(_M3, text):
        return ()
    if has(r"\b(?:stock|level|outstanding|saldo|nivel|existencias|millions?|millones)\b", text):
        return ("m3_stock",)
    if has(r"\b(?:annual|year on year|year over year|yoy|interanual|anual|grew|growth|crec\w*|revis\w*)\b", text):
        return ("m3_annual",)
    return ()


def _gdp_measures(text: str) -> tuple[Measure, ...]:
    if has(r"\b(?:consum\w*)\b", text):
        return ("gdp_consumption",) if has(_CONTRIBUTION, text) else ()
    if not has(_GDP, text) or has(r"\b(?:nominal|per capita|employment|empleo)\b", text):
        return ()
    return ("gdp_quarterly",)


def _fx_measures(text: str) -> tuple[Measure, ...]:
    if has(r"\b(?:swaps?|liquid(?:ity|ez))\b", text):
        return ("swap_liquidity",) if not has(r"\bspot\b|eur\s*/\s*usd", text) else ()
    if has(r"\b(?:spot|eur.?usd|exchange rate|tipo de cambio)\b", text):
        if has(_MOVE, text) or has(r"\b(?:rise|rose|fell|fall|subi[oó]|cay[oó]|increase\w*|depreci\w*|appreci\w*|what happened)\b", text):
            return ("spot_move",)
        return ("spot_level",)
    return ()


def _series_requested(text: str, indicator: str, measures: tuple[Measure, ...]) -> bool:
    if not has(r"\b(?:official series|serie oficial|endpoint|beginning.to.end|de inicio a fin|entre los extremos)\b", text):
        return False
    direct: dict[str, tuple[Measure, ...]] = {
        "deposit-rate": ("deposit_decision", "deposit_move"), "hicp-yoy": ("hicp_annual",),
        "real-gdp-qoq": ("gdp_quarterly",), "m3-stock": ("m3_stock",), "eur-usd": ("spot_move",),
    }
    return len(measures) == 1 and measures[0] in direct[indicator] and has(_MOVE, text)


def _causal_request(text: str) -> bool:
    causal = has(_CAUSAL, text)
    if has(_ATTRIBUTED, text) and has(r"\b(?:cite|identify|attribute|drivers?|factors?|explanation|explicacion|factor(?:es)?|cito|atribuyo)\b", text) and not has(
        r"\b(?:prove|establish|cuantific\w*|demuestr\w*|counterfactual|how much|cuanto)\b", text
    ):
        causal = False
    if has(r"\b(?:que tasa|which rate|what rate)\b.*\b(?:atribuyo|assigned|reported)\b.*\b(?:publication|publicacion|release|comunicado)\b", text):
        causal = False
    return causal


def _scope_matches(text: str, scope: Scope) -> bool:
    return not (
        any(geo != scope.geography and has(pattern, text) for geo, pattern in _GEO.items())
        or any(event != scope.event and has(pattern, text) for event, pattern in _EVENTS.items())
        or has(_OTHER_GEO, text)
    )


def _requested_measures(text: str, scope: Scope) -> tuple[Measure, ...]:
    if scope.indicator == "hicp-yoy":
        component = has(_CONTRIBUTION, text) and (has(_ENERGY, text) or has(_FOOD, text))
        subject = re.sub(r"\b(?:excluding|excluyendo) (?:the |los )?(?:tax changes|cambios (?:de|en) impuestos)\b", "", text)
        subject = re.sub(r"\b(?:core inflation|inflacion subyacente)\b", "", subject)
        if ((has(_NARROW_PRICE, subject) and not component) or has(_BASKET_EXCLUSION, subject) or has(
            rf"{_NON_ANNUAL_HICP}|\b(?:cpi|ipc|annualized|anualizad\w*)\b", text,
        )):
            return ()
        return _hicp_measures(text)
    if scope.indicator == "deposit-rate":
        return _policy_measures(text)
    if scope.indicator == "m3-stock":
        return _money_measures(text)
    if scope.indicator == "eur-usd":
        return _fx_measures(text)
    if scope.event == "genai" and not has(_GDP, text):
        return _ai_measures(text)
    if has(r"\b(?:year.on.year|yoy|interanual|annualized|anualizad\w*|gdp level|nivel del pib)\b", text):
        return ()
    return _gdp_measures(text)


def _coordinated_periods(text: str, timing: Times) -> tuple[Times, ...]:
    references = [(when, start, end) for role, when, start, end in _time_mentions(text) if role == "reference"]
    if len(references) < 2 or len(references) != len(timing.reference):
        return (timing,)
    joins = [text[left[2]:right[1]] for left, right in zip(references, references[1:])]
    if not all(re.fullmatch(r"(?:,\s*|\s+(?:and|y)\s+)(?:(?:in|en)\s+)?", join) for join in joins):
        return (timing,)
    if has(r"\b(?:between|entre|from|desde)\s*(?:the |el )?$", text[:references[0][1]]):
        return (timing,)
    years = {when.year for when, _, _ in references if when.year is not None}
    if len(years) == 1:
        year = next(iter(years))
        return tuple(replace(timing, reference=(replace(when, year=when.year or year),)) for when, _, _ in references)
    return tuple(replace(timing, reference=(when,)) for when, _, _ in references)


def _modality(text: str, measure: Measure) -> ClaimType:
    if measure == "ai_hypothesis":
        return "unconfirmed_hypothesis"
    if measure == "deposit_effective":
        return "observed"
    if measure in {"deposit_intention", "app_schedule"} or has(_EXPECTATION, text):
        return "projection_or_expectation"
    if measure in {"hicp_diagnosis", "ai_tasks"}:
        return "institutional_interpretation"
    return "observed"


def measure_variant(measure: Measure) -> Variant | None:
    if measure in {"deposit_decision", "deposit_effective", "deposit_in_force", "deposit_move", "deposit_intention"}:
        return "nominal"
    if measure in {"hicp_annual", "hicp_energy", "hicp_food"}:
        return "headline"
    if measure in {"gdp_quarterly", "gdp_consumption"}:
        return "real"
    return None


def _technology(measure: Measure) -> Technology | None:
    if measure in {"ai_enterprise", "ai_automation"}:
        return "any_ai"
    if measure == "ai_language":
        return "natural_language_generation"
    if measure in {"ai_household", "ai_purpose", "ai_hypothesis", "ai_tasks"}:
        return "generative_ai"
    return None


def _measure_statistic(text: str, measure: Measure, *, requested: bool = False) -> Statistic:
    kind = statistic(text)
    if measure in {"deposit_move", "series_change", "spot_move"}:
        return "change" if kind == "point" else kind
    # The pricing formula may refer to an average of a different instrument.
    if measure in {"tltro_pricing", "ltro_pricing"}:
        return "point"
    if measure == "hicp_annual" and (has(
        r"\b(?:change in (?:the )?|variacion de (?:la |el )?|cambio (?:en|de) (?:la |el )?)"
        r"(?:(?:annual|headline|interanual|general|hicp|ipca|inflation|inflacion) )*(?:rate|tasa)\b",
        text,
    ) or (requested and _hicp_change_request(text))):
        return "change" if kind == "point" else "other"
    return kind


def _hicp_change_request(text: str) -> bool:
    if has(_HICP_COMPARISON, text) or has(_RATE_RATIO, text):
        return True
    # The annual change of the price index defines the annual inflation rate.
    # A change of that rate requires a distinct calculation.
    text = re.sub(r"\b(?:annual|year[- ](?:on|over)[- ]year) (?:change|variation)\b", "annual rate", text)
    if has(r"\b(?:change|variation|increase|decrease|drop) in\b|\b(?:cambio|variacion|aumento|caida) (?:del?|en)\b", text):
        return True
    return not has(r"\b(?:year[- ](?:on|over)[- ]year|interanual|anual)\b", text) and has(
        r"\b(?:how much did .{0,70}?\b(?:rise|fall|change|increase|decrease|go (?:up|down))|"
        r"cuanto (?:subio|bajo|aumento|cayo|vario|cambio))\b", text,
    )


def _comparison_direction(text: str) -> Literal["higher", "lower"] | None:
    if has(r"\b(?:higher|exceed\w*|mayor|super[oa])\b", text):
        return "higher"
    if has(r"\b(?:lower|menor)\b", text):
        return "lower"
    return None


def _comparison_operation(base: When, target: When, text: str) -> RateComparison:
    absolute = has(r"\b(?:absolute (?:difference|change|gap)|diferencia absoluta|valor absoluto)\b", text)
    return RateComparison(base, target, _comparison_direction(text), absolute)


def _rate_comparison(text: str, timing: Times) -> RateComparison | None:
    if any(has(pattern, text) for pattern in (_DEPOSIT, _GDP, _M3, _ENERGY, _FOOD)):
        return None
    refs = timing.reference
    if len(refs) == 1 and has(_PREVIOUS_MONTH, text):
        target = refs[0]
        if target.year is not None and target.month is not None and target.day is None:
            year = target.year - int(target.month == 1)
            return _comparison_operation(When(year, target.month - 1 or 12), target, text)
    if len(refs) != 2 or any(when.month is None or when.day is not None for when in refs):
        return None
    first, second = refs
    mentions = [(left, right) for role, _, left, right in _time_mentions(text) if role == "reference"]
    if len(mentions) != 2:
        return None
    join = text[mentions[0][1]:mentions[1][0]]
    left = text[:mentions[0][0]]
    chronological = has(r"\b(?:from|between|de|desde|entre)\s*$", left) and has(r"\b(?:to|and|a|y)\b", join)
    target_first = has(r"\b(?:than|que|versus|vs|exceed\w*|super[oa]|relative to|respecto (?:a|al|de|del))\b", join)
    if not chronological and not target_first:
        return None
    if first.year is None or second.year is None:
        # Never guess a year across a calendar boundary.
        if (chronological and first.month >= second.month) or (target_first and first.month <= second.month):
            return None
        year = first.year or second.year
        if year is None:
            return None
        first, second = replace(first, year=year), replace(second, year=year)
    if first == second:
        return None
    if chronological:
        return _comparison_operation(first, second, text)
    return _comparison_operation(second, first, text)


def _operation(
    text: str, measure: Measure, requested_statistic: Statistic, unit: Unit | None, *, elliptical: bool = False,
) -> Operation | None:
    if requested_statistic == "average":
        return "temporal_average"
    if requested_statistic == "point":
        return "observed_level"
    if requested_statistic != "change":
        return None
    if measure == "hicp_annual" and has(_RATE_RATIO, text):
        return "rate_ratio"
    if measure == "hicp_annual" and (elliptical or has(_RATE_TARGET, text) or unit == "percentage_points"):
        if unit == "percentage_points":
            return "percentage_point_difference"
        if unit == "percent" or has(
            r"\b(?:relative (?:change|increase|decrease)|proportional change|(?:cambio|variacion) relativ[oa])\b", text,
        ):
            return "percentage_change"
        return "rate_difference"
    return "level_change"


def _elliptical_hicp_rate(text: str) -> bool:
    text = _DATES.sub("", text)
    text = re.sub(r"\b(?:in|for|en|de|del) +(?=(?:[.?]|$))", "", text)
    return re.fullmatch(
        r"(?:(?:what (?:was|is)|give|report|cual fue|indica|indique) )?(?:the |la )?"
        r"(?:headline (?:annual |year[- ]on[- ]year )?rate|(?:annual|year[- ]on[- ]year) (?:headline )?rate|"
        r"tasa (?:interanual(?: general)?|general interanual))"
        r"(?: (?:in percent(?:age)?|en porcentaje))?\s*[.?]?",
        normalize(text),
    ) is not None


def _elliptical_rate_comparison(text: str, local: Times, antecedent: Requirement) -> RateComparison | None:
    if len(antecedent.timing.reference) != 1:
        return None
    target, = antecedent.timing.reference
    if target.year is None or target.month is None or target.day is not None:
        return None
    if not local.reference:
        return _rate_comparison(text, Times(reference=(target,)))
    if len(local.reference) != 1:
        return None
    base, = local.reference
    if base.month is None or base.day is not None:
        return None
    if base.year is None:
        if base.month >= target.month:
            return None
        base = replace(base, year=target.year)
    return _comparison_operation(base, target, text)


def _policy_timing(text: str, timing: Times, measure: Measure) -> Times:
    if measure not in {"deposit_decision", "deposit_effective"} or not has(_ANNOUNCED, text):
        return timing
    target = tuple(when for role, when, start, _ in _time_mentions(text)
                   if role == "reference" and has(r"\b(?:for|para(?: el)?)\s*$", text[:start]))
    if target:
        return replace(timing, reference=tuple(when for when in timing.reference if when not in target),
                       effective=tuple(dict.fromkeys((*timing.effective, *target))))
    return timing


def _vintage_requirements(text: str, timing: Times, measure: Measure) -> tuple[tuple[Times, bool, bool], ...]:
    revised = has(_REVISED, text)
    flash = has(r"\b(?:flash|primera estimacion|first estimate)\b", text)
    if len(timing.publication) <= 1 or measure != "gdp_quarterly":
        periods = ((timing,) if measure in {"deposit_intention", "deposit_effective", "deposit_in_force", "series_change", "spot_move"}
                   else _coordinated_periods(text, timing))
        return tuple((period, revised, flash) for period in periods)
    publications = [(when, left, right) for role, when, left, right in _time_mentions(text) if role == "publication"]
    splits = []
    for index, (when, left, right) in enumerate(publications):
        # Local vintage words must belong to the publication operand, not to the preceding one.
        before = text[publications[index - 1][2] if index else 0:left]
        after = text[right:publications[index + 1][1] if index + 1 < len(publications) else len(text)]
        before = re.split(r"\b(?:and|y)\b", before)[-1]
        after = re.split(r"\b(?:and|y)\b", after)[0]
        local = f"{before} {after}"
        year = when.year
        if year is None and len(timing.reference) == 1:
            year = timing.reference[0].year
        publication = replace(when, year=year)
        splits.append((replace(timing, publication=(publication,)), has(_REVISED, local),
                       has(r"\b(?:flash|primera estimacion|first estimate)\b", local)))
    return tuple(splits)


def _interpret_clause(
    text: str, scope: Scope, inherited: Times, *, hicp_antecedent: Requirement | None = None,
) -> Intent:
    is_counterfactual = counterfactual(text)
    causal = is_counterfactual or _causal_request(text)
    if not _scope_matches(text, scope) or exclusive(text):
        return Intent((), causal, unsupported=True)
    requested_adjustment = adjustment(text)
    if requested_adjustment == "other":
        return Intent((), causal, unsupported=True)
    local_timing = times(text)
    timing = local_timing
    timing = Times(timing.reference or inherited.reference, timing.publication or inherited.publication,
                   timing.effective or inherited.effective, timing.as_of or inherited.as_of)
    if timing.as_of and not all(day.includes_day(date.fromisoformat(scope.cutoff)) for day in timing.as_of):
        return Intent((), causal, unsupported=True)
    institutions = tuple(name for name, pattern in _INSTITUTIONS.items() if has(pattern, text))
    if len(institutions) > 1:
        return Intent((), causal, unsupported=True)
    measures = _requested_measures(text, scope)
    comparison_ellipsis = (
        not measures and hicp_antecedent is not None and hicp_antecedent.measure == "hicp_annual"
        and _COMPARISON_ELLIPSIS.match(text) is not None
    )
    elliptical = not measures and hicp_antecedent is not None and (
        comparison_ellipsis or _elliptical_hicp_rate(text)
    )
    if elliptical:
        measures = ("hicp_annual",)
        if not timing.publication:
            timing = replace(timing, publication=hicp_antecedent.timing.publication)
    objects: tuple[tuple[Measure, Purpose | None], ...] = tuple((measure, None) for measure in measures)
    unmatched: tuple[str, ...] = ()
    if _ADOPTION_MEASURES.intersection(measures):
        objects, unmatched = _adoption_objects(text)
        measures = tuple(measure for measure, _ in objects)
    elif {"hicp_energy", "hicp_food"}.intersection(measures):
        contribution_objects = _contribution_objects(text)
        if contribution_objects:
            measures, unknown_parts = contribution_objects
            if unknown_parts:
                unmatched = (_contribution_actors(text) or text,)
            objects = tuple((measure, None) for measure in measures)
        if has(_NARROW_PRICE, text) and not unmatched:
            unmatched = (text,)

    if "deposit_intention" in measures and len(timing.reference) > 1 and not timing.publication:
        timing = Times(timing.reference[1:], timing.reference[:1], timing.effective, timing.as_of)
    if not _supported_subject(text, measures):
        return Intent((), causal, unsupported=True)
    if _series_requested(text, scope.indicator, measures):
        measures = ("series_change",)
        objects = (("series_change", None),)
    explicit_units = units(text, adoption=bool(_ADOPTION_MEASURES.intersection(measures)))
    unit = explicit_units[0] if len(explicit_units) == 1 else None
    if len(explicit_units) > 1:
        return Intent((), causal, unsupported=True)
    whole_period = has(r"\b(?:throughout|(?:entire|whole|full) (?:year|quarter|month|period)|todo el|durante todo|cada mes|each month|every month)\b", text)
    premise = re.search(r"(?<![\d.])-?\d+[.,]\d+\s*%", text) if "deposit_effective" in measures else None
    area = re.search(r"\bea(?:19|20|21)\b", text)
    population = _adoption_population(text)
    requested_variant = statistical_variant(text)
    requirements = []
    for measure, purpose in objects:
        requested_statistic = _measure_statistic(text, measure, requested=True)
        if comparison_ellipsis and requested_statistic == "point":
            requested_statistic = "change"
        operation = _operation(text, measure, requested_statistic, unit, elliptical=elliptical)
        comparison = (
            _rate_comparison(text, timing)
            if operation in {"rate_difference", "percentage_point_difference", "percentage_change"} else None
        )
        if comparison_ellipsis:
            comparison = _elliptical_rate_comparison(text, local_timing, hicp_antecedent)
            if hicp_antecedent.timing.publication and timing.publication != hicp_antecedent.timing.publication:
                comparison = None
        policy_timing = _policy_timing(text, timing, measure)
        vintages = _vintage_requirements(text, policy_timing, measure)
        if measure == "hicp_annual" and requested_statistic == "change":
            vintages = ((policy_timing, has(_REVISED, text), False),)
        for measure_timing, revised, flash in vintages:
            requirements.append(Requirement(
                measure, measure_timing, unit, revised=revised, flash=flash,
                institution=institutions[0] if institutions else hicp_antecedent.institution if elliptical else None,
                premise=Decimal(premise.group().removesuffix("%").strip().replace(",", ".")) if premise else None,
                modality=_modality(text, measure),
                population=population if measure in _ADOPTION_MEASURES else None,
                area_definition=area.group() if area else hicp_antecedent.area_definition if elliptical else None,
                polarity="affirmative" if is_counterfactual else polarity(text),
                adjustment=requested_adjustment or (hicp_antecedent.adjustment if elliptical else None),
                variant=requested_variant or (hicp_antecedent.variant if elliptical else None) or measure_variant(measure),
                statistic=requested_statistic,
                subgroup=_adoption_subgroup(text, population) if measure in _ADOPTION_MEASURES else None,
                purpose=purpose, technology=_technology(measure), indicator=scope.indicator, geography=scope.geography,
                description=text, whole_period=whole_period,
                announced=has(_ANNOUNCED, text) and not has(_ACTUAL_POLICY, text),
                denominator=_adoption_denominator(text) if measure in _ADOPTION_MEASURES else None,
                average_method=average_method(text) if requested_statistic == "average" else None,
                operation=operation, comparison=comparison,
            ))
    return Intent(tuple(requirements), causal, unsupported=not requirements, unknown_parts=bool(unmatched), unmatched_parts=unmatched)


def interpret(question: str, scope: Scope) -> Intent:
    text = normalize(question)
    text = re.sub(
        rf"\b({_ORDINAL}) (and|y) ({_ORDINAL}) (quarter|trimestre)s?\b",
        r"\1 \4 \2 \3 \4", text,
    )
    # A boundary or approximate date describes a window. A single claim's
    # reference period cannot be treated as that window's complete coverage.
    if any(role not in {"as_of", "effective"} and relative_time(text, start, end)
           for role, _, start, end in _time_mentions(text)):
        return Intent((), counterfactual(text) or _causal_request(text), unsupported=True)
    inherited = times(text)
    if scope.indicator == "real-gdp-qoq" and len(inherited.publication) == 2 and len(inherited.reference) == 1 and has(
        r"\b(?:flash|preliminary|preliminar)\b", text,
    ) and has(_REVISED, text) and len(re.findall(_GDP, text)) == 1 and not has(
        r"\b(?:and|y) (?:what|which|how|why|que|cual|cuando|como|cuant[oa]s?)\b", text,
    ):
        return _interpret_clause(text, scope, Times())
    # Preserve conjunctive questions as independent requirements. A recognized
    # clause cannot erase another requested fact.
    parts = re.split(
        rf"(?:\b(?:and|y|plus|as well as|asi como) |;\s*)(?=(?:what|which|how|{_COMPARISON_PROMPT}|when|why|did|does|"
        r"que|cual|cuando|como|cuant[oa]s?|en cuanto|por que|"
        r"its (?:change|difference)|su (?:variacion|diferencia)|"
        r"the|la|el|spot|eur.?usd|exchange rate|real gdp|pib|m3|headline|constant[- ]tax|"
        r"net (?:of|after) tax|after[- ]tax|despues de impuestos|net[oa] de impuestos|a cuanto)\b)", text,
    )
    if len(parts) == 1:
        return _interpret_clause(text, scope, Times())
    if len(inherited.reference) > 1 or len(inherited.publication) > 1:
        leading_source = has(r"^[¿? ]*(?:based on|according to|using|segun|con base en|a partir del)\b", text)
        publication = inherited.publication if leading_source and len(inherited.publication) == 1 else ()
        inherited = Times(publication=publication, as_of=inherited.as_of)
    if scope.indicator == "deposit-rate" and has(_DEPOSIT, parts[0]) and has(_ANNOUNCED, parts[0]):
        # A same-date companion can elide the named instrument but not its qualifier.
        parts = [parts[0], *(
            f"{part.rstrip(' ?')} deposit facility rate at the decision?"
            if statistical_variant(part) in {"net_of_tax", "gross_of_tax"} and not has(_DEPOSIT, part) else part
            for part in parts[1:]
        )]
    clauses = []
    hicp_antecedent = None
    for part in parts:
        clause = _interpret_clause(part, scope, inherited, hicp_antecedent=hicp_antecedent)
        clauses.append(clause)
        hicp_antecedent = (clause.requirements[0] if scope.indicator == "hicp-yoy" and has(_HICP, part)
                           and clause.requirements and not clause.causal else None)
    requirements = tuple(dict.fromkeys(req for clause in clauses for req in clause.requirements))
    unmatched = tuple(item for part, clause in zip(parts, clauses, strict=True)
                      for item in (clause.unmatched_parts or ((part,) if clause.unsupported else ())))
    return Intent(requirements, any(c.causal for c in clauses), unsupported=not requirements,
                  unknown_parts=bool(unmatched), unmatched_parts=unmatched)


_COVERAGE_DATE = rf"{_YEAR}-\d{{2}}-\d{{2}}"
_RATE_VALUE = r"(?P<value>-?\d{1,5}(?:[.,]\d{1,12})?)\s*%"
_RATE_INTERVAL = re.compile(
    rf"(?:the )?ecb deposit facility rate (?P<verb>was|averaged) {_RATE_VALUE} "
    rf"from (?P<start>{_COVERAGE_DATE}) through (?P<end>{_COVERAGE_DATE})"
    r"(?: on a calendar-day-weighted basis)?\.?"
)
_RATE_DAY = re.compile(
    rf"on (?P<start>{_COVERAGE_DATE}), (?:the )?ecb deposit facility rate (?P<verb>was) {_RATE_VALUE}\.?"
)


def _reported_rate(claim: Claim, text: str) -> tuple[RateCoverage, Statistic] | None:
    """Admit only explicit, completed calendar-day coverage in reviewed observations."""
    match = _RATE_INTERVAL.fullmatch(text) or _RATE_DAY.fullmatch(text)
    if not match or claim.type != "observed":
        return None
    start = match["start"]
    end = match.groupdict().get("end") or start
    if (start, end) != (claim.historical_period.start, claim.historical_period.end) or not start <= end <= claim.publication_date:
        return None
    coverage = RateCoverage(date.fromisoformat(start), date.fromisoformat(end), Decimal(match["value"].replace(",", ".")))
    return coverage, "average" if match["verb"] == "averaged" else "point"


def _day_in(mentions: tuple[When, ...], published: date, *, effective: bool = False) -> date | None:
    days = set()
    for mention in mentions:
        if mention.day is None or mention.month is None:
            continue
        year = published.year + int(effective and mention.month < published.month)
        try:
            days.add(mention.bounds(year)[0])
        except ValueError:
            return None
    return next(iter(days)) if len(days) == 1 else None


def _claim_purposes(measure: Measure, text: str) -> tuple[Purpose | None, ...]:
    if measure == "ai_automation":
        combined = re.search(rf"(?:{_WORKFLOW} (?:or|o) {_DECISION_SUPPORT}|{_DECISION_SUPPORT} (?:or|o) {_WORKFLOW})", text)
        if combined:
            return ("workflow_or_decision_support",)
        # A single category must be attributable as the requested measured purpose.
        if has(_WORKFLOW, text) and not has(_DECISION_SUPPORT, text):
            return ("workflow",)
        if has(_DECISION_SUPPORT, text) and not has(_WORKFLOW, text):
            return ("decision_support",)
        return ()
    if measure == "ai_purpose":
        categories = re.findall(
            r"\b(personal purposes?|work(?: purposes?)?|formal education) by \d+(?:\.\d+)?%"
            r"|\b(work(?: purposes?)?|formal education) by \d+(?:\.\d+)?%", text,
        )
        return tuple(purpose for left, right in categories if (purpose := survey_purpose(left or right)))
    return (None,)


def _claim_units(measure: Measure, stated: tuple[Unit, ...]) -> tuple[Unit, ...]:
    expected = {
        "ai_enterprise": "percent", "ai_language": "percent", "ai_automation": "percent", "ai_household": "percent",
        "ai_purpose": "percent", "hicp_energy": "percentage_points", "hicp_food": "percentage_points",
        "hicp_annual": "percent", "gdp_quarterly": "percent", "gdp_consumption": "percentage_points",
        "m3_annual": "percent", "m1_annual": "percent", "m1_contribution": "percentage_points",
        "deposit_decision": "percent", "deposit_in_force": "percent",
    }.get(measure)
    return tuple(unit for unit in stated if unit == expected) if expected else stated


def claim_predicates(claim: Claim) -> tuple[Predicate, ...]:
    """Describe only the positive statement in the reviewed claim."""
    text = normalize(claim.text)
    timing = times(text)
    stated = units(text)
    qualifiers = normalize(claim.qualifiers)
    reported = _reported_rate(claim, text) if "deposit-rate" in claim.indicators else None
    published = date.fromisoformat(claim.publication_date)
    effective = _day_in(timing.effective, published, effective=True)
    decision = _day_in(timing.reference, published)
    measures: tuple[Measure, ...] = ()
    if claim.type == "observed":
        if "eur-usd" in claim.indicators:
            measures = _fx_measures(text)
        elif has(_AI, text) or has(_LANGUAGE, text):
            measures = _ai_measures(text)
        elif has(_M3, text) or has(r"\bm1\b", text):
            measures = _money_measures(text)
        elif has(_GDP, text) and not has(r"\bcontainment measures\b", text):
            measures = _gdp_measures(text)
        elif has(_HICP, text):
            measures = _hicp_measures(text)
        elif has(_DEPOSIT, text) or has(r"\b(?:tltro|ltro)\b", text):
            measures = _policy_measures(text)
            if measures == ("deposit_decision",) and effective:
                measures = ("deposit_decision", "deposit_effective")
            elif measures == ("deposit_in_force",) and not reported:
                measures = ()
    elif claim.type == "projection_or_expectation":
        measures = _policy_measures(text)
    elif claim.type == "institutional_interpretation":
        if has(_AI, text):
            measures = _ai_measures(text)
        elif has(r"\b(?:drivers of price pressure|inflation outlook)\b", text):
            measures = ("hicp_diagnosis",)
    elif claim.type == "unconfirmed_hypothesis":
        measures = _ai_measures(text)
    population = _adoption_population(text)
    variant = statistical_variant(text)
    text_adjustment, editorial_adjustment = adjustment(text), adjustment(qualifiers)
    stated_adjustment = (
        "other" if text_adjustment and editorial_adjustment and text_adjustment != editorial_adjustment
        else text_adjustment or editorial_adjustment
    )
    return tuple(
        Predicate(measure, _claim_units(measure, stated), timing,
                  previous_reference=has(r"\b(?:after|from|compared with|same september release)\b", text),
                  flash=has(r"\bflash\b", text),
                  population=population if measure in _ADOPTION_MEASURES else None,
                  polarity=polarity(text), adjustment=stated_adjustment,
                  variant=variant or measure_variant(measure),
                  statistic=reported[1] if reported else _measure_statistic(text, measure),
                  subgroup=_adoption_subgroup(text, population) if measure in _ADOPTION_MEASURES else None,
                  purpose=purpose, technology=_technology(measure),
                  rate_coverage=reported[0] if reported else None,
                  decision_date=decision if measure in {"deposit_decision", "deposit_effective", "deposit_in_force"} else None,
                  revised=has(_REVISED, text) or has(r"^revised\b", qualifiers), effective_date=effective,
                  denominator=_adoption_denominator(text) if measure in _ADOPTION_MEASURES else None,
                  average_method="calendar_day_weighted" if reported and reported[1] == "average"
                  and text.rstrip(".").endswith("on a calendar-day-weighted basis") else None)
        for measure in measures
        for purpose in _claim_purposes(measure, text)
    )
