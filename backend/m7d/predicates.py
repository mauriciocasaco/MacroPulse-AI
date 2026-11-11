"""Controlled predicates for the experimental Spanish/English evidence gate."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import re
from typing import Literal
import unicodedata

from backend.m7c.corpus import Scope
from scripts.m7b_schema import Claim, ClaimType, Institution

from .question_constraints import Adjustment, Polarity, adjustment, counterfactual, exclusive, operands, polarity, relative_time


Unit = Literal["percent", "percentage_points", "basis_points", "count", "level"]
Population = Literal["enterprises", "enterprises_10_plus", "households", "persons", "persons_16_74"]
Measure = Literal[
    "hicp_annual", "hicp_energy", "hicp_food", "hicp_energy_rate", "hicp_diagnosis",
    "gdp_quarterly", "gdp_consumption", "m3_annual", "m3_stock", "m1_annual", "m1_contribution",
    "deposit_decision", "deposit_effective", "deposit_intention", "deposit_move", "app_schedule",
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
    rf"|(?:(?P<qyear>{_YEAR})[ -]?)?q(?P<q>[1-4])(?: (?:of |de )?(?P<qy2>{_YEAR}))?"
    rf"|(?P<ordinal>{_ORDINAL})[- ](?:quarter|trimestre)(?: (?:of |de )?(?P<oy>{_YEAR}))?"
    rf"|(?:(?P<pre>{_DAY}) (?:de )?)?(?P<month>{_MONTH})(?: (?P<post>{_DAY})(?!\d))?"
    rf"(?:[ ,]+(?:de )?(?P<my>{_YEAR}))?"
    rf"|(?P<year>{_YEAR}))(?![\w-])"
)
_VINTAGE_BEFORE = (
    r"(?:release|publicaci[oó]n|publicad[oa]|published|released|comunicado|"
    r"estimaci[oó]n|estimate|anunciad[oa]|announc(?:ed|ement)|bolet[ií]n)"
    r"(?: (?:in|on|of|en|del?|of the))?\s*$"
)
_VINTAGE_AFTER = r"^\s*(?:(?:flash|preliminary|final) )?(?:release|estimate|publication|announcement|comunicado)\b"
_AS_OF = r"(?:as of|disponible(?:s)? hasta(?: el)?|con corte(?: al)?|a fecha(?: de)?)\s*$"
_EFFECTIVE = r"(?:effective(?: from| on)?|vigente desde|en vigor(?: el| desde)?|aplicable desde)\s*$"


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
_FIRMS = r"\b(?:firms?|enterprises?|business(?:es)?|compan(?:y|ies)|empresas?)\b"
_PEOPLE = r"\b(?:people|persons?|personas?|individuals?|individuos?|adults?|adultos?|residents?|residentes?)\b"
_HOUSEHOLDS = rf"(?:{_PEOPLE}|\b(?:households?|hogares?)\b)"
_LANGUAGE = r"\b(?:natural language generation|language generation|generacion (?:de )?(?:lenguaje|texto))\b"
_AUTOMATION = r"\b(?:automated workflows?|decision support|flujos automatizados|apoyo a decisiones)\b"
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
_PERCENT_POINTS = r"\b(?:percentage points?|puntos porcentuales?|pp)\b"
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
_ADOPTION_MEASURES = frozenset({"ai_enterprise", "ai_language", "ai_automation", "ai_household", "ai_purpose"})
_ADOPTION_VERB = r"\b(?:used?|using|adopt(?:ed|ing)?|usa(?:ban|ron|ba)?|uso|utiliz(?:aba|aban|aron|o|ar))\b"
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


@dataclass(frozen=True)
class Intent:
    requirements: tuple[Requirement, ...]
    causal: bool
    unsupported: bool = False
    whole_period: bool = False
    unknown_parts: bool = False


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
    if has(r"\b(?:pace|ritmo|extent|magnitud)\b", text) and has(r"\b(?:choices|decisions|decisiones)\b", text):
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
        "cuantas", "cuantos", "entre", "en", "por", "que", "which", "what", "did", "does",
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


def _unsupported_population(text: str) -> bool:
    if has(_SUBPOPULATION, text) or has(r"\b(?:median|mediana|percentile|percentil|by industry|por sector|by member state)\b", text):
        return True
    if has(_FIRMS, text):
        return _enterprise_qualifier(text) or (
            has(r"\b(?:employees|empleados)\b", text) and not has(_FIRM_THRESHOLD, text)
        )
    if has(_HOUSEHOLDS, text) and has(r"\b(?:aged?|ages?|edad(?:es)?|years old|anos de edad)\b", text):
        return not has(_AGE_THRESHOLD, text)
    return False


def _technology_measure(text: str, population: Population | None) -> Measure | None:
    text = text.strip(" ,.;?").removeprefix("the ").removeprefix("la ").removeprefix("las ")
    if re.fullmatch(_LANGUAGE, text):
        return "ai_language"
    if re.fullmatch(_AUTOMATION, text):
        return "ai_automation"
    if population in {"persons", "persons_16_74", "households"} and re.fullmatch(
        rf"{_GENAI}(?: (?:tools?|herramientas?))?"
        r"(?: (?:for|para) (?:personal purposes?|propositos personales|work|trabajo|formal education|educacion formal))?",
        text,
    ):
        return "ai_household"
    if population in {"enterprises", "enterprises_10_plus"} and re.fullmatch(
        r"(?:(?:any|all|some|at least one|alguna|todas las) )?"
        r"(?:(?:technolog(?:y|ies)|tecnologias?) (?:de )?)?"
        r"(?:ai|ia|artificial intelligence|inteligencia artificial)"
        r"(?: (?:technolog(?:y|ies)|tools?|herramientas?))?",
        text,
    ):
        return "ai_enterprise"
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


def _adoption_objects(text: str) -> tuple[tuple[Measure, ...], bool] | None:
    """Recognize enumerated technology operands without inheriting the first operand."""
    tail = _adoption_tail(text)
    if not tail:
        return None
    individual = has(r"\b(?:respectively|separately|each|por separado|respectivamente|cada)\b", tail)
    tail = re.sub(r"\b(?:respectively|separately|por separado|respectivamente)\b", "", tail)
    objects = operands(tail)
    population = _adoption_population(text)
    recognized = tuple(_technology_measure(item, population) for item in objects)
    if len(objects) < 2:
        # Preserve an explicit survey purpose after a recognized use verb.
        return None if recognized and recognized[0] else ((), True)
    return tuple(dict.fromkeys(item for item in recognized if item)), not individual or any(item is None for item in recognized)


def _adoption_question(text: str) -> bool:
    return has(r"\b(?:us(?:e[ds]?|ing|o|a[bdn]\w*|aron)|utiliz\w*|adopt\w*|adopc\w*)\b", text)


def _aggregate_hicp(text: str) -> bool:
    # A specific sector/product after the rate noun is a different measurement.
    # Allow only known kinds of modifiers in that syntactic position.
    narrowed = re.search(rf"{_HICP} (?:rate |tasa )?(?:for|of|among|in|de|del|en|entre|para) (?:the |la |el |las |los )?(\w+)", text)
    if narrowed and narrowed.group(1) not in {
        *_MONTH_NUMBER, "euro", "eurozona", "zona", "ea19", "ea20", "eu", "ue", "union",
        "2020", "2022", "2023", "q1", "q2", "q3", "q4", "lockdown", "confinamiento",
    } and not narrowed.group(1).isdecimal():
        return False
    if has(r"\b(?:median|mediana|mean|media aritmetica|percentile|percentil|across|por ciudades|entre ciudades)\b", text):
        return False
    if has(r"\b(?:price|precios?) (?:of|for|de|del|para)\b", text):
        return False
    modifier = re.search(r"\b([a-z]+) (?:inflation|inflacion)\b", text)
    return not modifier or modifier.group(1) in {
        "annual", "year", "headline", "aggregate", "total", "overall", "general", "euro", "area", "eurozone",
        "hicp", "ipca", "ea19", "ea20", "ea21", "was", "did", "is", "what", "how", "the", "reported",
        "annualized", "de", "la", "en", "para", "in", "rate", "tasa", "of", "to",
    }


def _supported_subject(text: str, measures: tuple[Measure, ...]) -> bool:
    if "hicp_annual" in measures and not _aggregate_hicp(text):
        return False
    if "gdp_quarterly" in measures:
        # The bare aggregate has a closed set of grammatical modifiers. A
        # geographic adjective in this position is a different target series.
        prefix = re.search(r"\b([a-z0-9]+)[ -]+(?:(?:quarter-on-quarter|quarterly|real|seasonally adjusted) )*gdp\b", text)
        if prefix and prefix.group(1) not in _AGGREGATE_GDP_PREFIX:
            return False
        locative = re.search(
            r"\b(?:gdp|pib)(?: (?:real|growth rate|growth|change|contraction|decline|drop|estimate|"
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
        }:
            return False
    if "deposit_decision" in measures:
        decision = has(
            r"\b(?:decid\w*|decision|decisi[oó]n|set|kept|held|hold|left|maintain\w*|"
            r"mant(?:uvo|en\w*)|fix\w*|fij\w*|rais\w*|lower\w*|subi[oó]|redujo|recort\w*|"
            r"cut|rate level|nivel|what (?:was|is) (?:the )?|cual (?:era|fue|es) (?:la |el )?)\b", text
        )
        if not decision:
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
        r"\b(?:did|does|do|(?:what|how large|how big) (?:is|was|are|were)|cual fue|cuant[oa]|que|como)\b",
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
    if has(_DEPOSIT, text) and has(
        r"\b(?:effective|effect|efectiv[oa]|aplicaba|aplic[oa]|aplicable|en vigor|entr[oó] en vigencia|vigente)\b", text,
    ):
        return ("deposit_effective",)
    if has(_EXPECTATION, text) and (has(_DEPOSIT, text) or has(r"\b(?:rates?|tasas?|increase|subida)\b", text)):
        return ("deposit_intention",)
    if not has(_DEPOSIT, text):
        return ()
    if has(_MOVE, text) or has(_BASIS_POINTS, text) or has(_PERCENT_POINTS, text):
        return ("deposit_move",)
    return ("deposit_decision",)


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
        if (has(_NARROW_PRICE, text) and not component) or has(_BASKET_EXCLUSION, text) or has(
            r"\b(?:month.on.month|mensual|mom|quarter.on.quarter)\b", text,
        ):
            return ()
        return _hicp_measures(text)
    if scope.indicator == "deposit-rate":
        return _policy_measures(text)
    if scope.indicator == "m3-stock":
        return _money_measures(text)
    if scope.indicator == "eur-usd":
        return _fx_measures(text)
    if scope.event == "genai" and not has(_GDP, text):
        return () if _unsupported_population(text) else _ai_measures(text)
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


def _interpret_clause(text: str, scope: Scope, inherited: Times) -> Intent:
    is_counterfactual = counterfactual(text)
    causal = is_counterfactual or _causal_request(text)
    if not _scope_matches(text, scope) or exclusive(text):
        return Intent((), causal, unsupported=True)
    requested_adjustment = adjustment(text)
    if requested_adjustment == "other":
        return Intent((), causal, unsupported=True)
    timing = times(text)
    timing = Times(timing.reference or inherited.reference, timing.publication or inherited.publication,
                   timing.effective or inherited.effective, timing.as_of or inherited.as_of)
    if timing.as_of and not all(day.includes_day(date.fromisoformat(scope.cutoff)) for day in timing.as_of):
        return Intent((), causal, unsupported=True)
    institutions = tuple(name for name, pattern in _INSTITUTIONS.items() if has(pattern, text))
    if len(institutions) > 1:
        return Intent((), causal, unsupported=True)
    measures = _requested_measures(text, scope)
    adoption_objects = _adoption_objects(text) if _ADOPTION_MEASURES.intersection(measures) else None
    unknown_parts = False
    if adoption_objects:
        measures, unknown_parts = adoption_objects
    elif {"hicp_energy", "hicp_food"}.intersection(measures):
        contribution_objects = _contribution_objects(text)
        if contribution_objects:
            measures, unknown_parts = contribution_objects
        unknown_parts |= has(_NARROW_PRICE, text)

    if "deposit_intention" in measures and len(timing.reference) > 1 and not timing.publication:
        timing = Times(timing.reference[1:], timing.reference[:1], timing.effective, timing.as_of)
    if not _supported_subject(text, measures):
        return Intent((), causal, unsupported=True)
    if _series_requested(text, scope.indicator, measures):
        measures = ("series_change",)
    explicit_units = units(text, adoption=bool(_ADOPTION_MEASURES.intersection(measures)))
    unit = explicit_units[0] if len(explicit_units) == 1 else None
    if len(explicit_units) > 1:
        return Intent((), causal, unsupported=True)
    whole_period = has(r"\b(?:throughout|(?:entire|whole|full) (?:year|quarter|month|period)|todo el|durante todo|cada mes|each month|every month)\b", text)
    premise = re.search(r"(?<![\d.])-?\d+[.,]\d+\s*%", text) if "deposit_effective" in measures else None
    area = re.search(r"\bea(?:19|20|21)\b", text)
    population = _adoption_population(text)
    requirements = tuple(
        Requirement(
            measure, measure_timing, unit, revised=has(_REVISED, text),
            flash=has(r"\b(?:flash|primera estimacion|first estimate)\b", text),
            institution=institutions[0] if institutions else None,
            premise=Decimal(premise.group().removesuffix("%").strip().replace(",", ".")) if premise else None,
            modality=_modality(text, measure),
            population=population if measure in _ADOPTION_MEASURES else None,
            area_definition=area.group() if area else None,
            polarity="affirmative" if is_counterfactual else polarity(text),
            adjustment=requested_adjustment,
        )
        for measure in measures
        for measure_timing in (
            (timing,) if measure in {"deposit_intention", "deposit_effective", "series_change", "spot_move"}
            else _coordinated_periods(text, timing)
        )
    )
    return Intent(requirements, causal, unsupported=not requirements, whole_period=whole_period, unknown_parts=unknown_parts)


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
    # Preserve conjunctive questions as independent requirements. A recognized
    # clause cannot erase another requested fact.
    parts = re.split(
        r"\b(?:and|y) (?=(?:what|which|how|when|why|did|does|que|cual|cuando|como|cuant[oa]s?|por que|"
        r"the|la|el|spot|eur.?usd|exchange rate|real gdp|pib|m3)\b)", text,
    )
    if len(parts) == 1:
        return _interpret_clause(text, scope, Times())
    inherited = times(text)
    if len(inherited.reference) > 1 or len(inherited.publication) > 1:
        inherited = Times(as_of=inherited.as_of)
    clauses = tuple(_interpret_clause(part, scope, inherited) for part in parts)
    requirements = tuple(dict.fromkeys(req for clause in clauses for req in clause.requirements))
    return Intent(requirements, any(c.causal for c in clauses), unsupported=not requirements,
                  whole_period=any(c.whole_period for c in clauses),
                  unknown_parts=any(c.unsupported or c.unknown_parts for c in clauses))


def claim_predicates(claim: Claim) -> tuple[Predicate, ...]:
    """Describe only the positive statement in the reviewed claim."""
    text = normalize(claim.text)
    timing = times(text)
    stated = units(text)
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
            if measures == ("deposit_effective",):
                measures = ("deposit_decision", "deposit_effective")
    elif claim.type == "projection_or_expectation":
        measures = _policy_measures(text)
    elif claim.type == "institutional_interpretation":
        if has(_AI, text):
            measures = _ai_measures(text)
        elif has(r"\b(?:drivers of price pressure|inflation outlook)\b", text):
            measures = ("hicp_diagnosis",)
    elif claim.type == "unconfirmed_hypothesis":
        measures = _ai_measures(text)
    return tuple(
        Predicate(measure, stated, timing,
                  previous_reference=has(r"\b(?:after|from|compared with|same september release)\b", text),
                  flash=has(r"\bflash\b", text),
                  population=_adoption_population(text) if measure in _ADOPTION_MEASURES else None,
                  polarity=polarity(text), adjustment=adjustment(text))
        for measure in measures
    )
