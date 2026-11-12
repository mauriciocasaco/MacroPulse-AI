"""Operators and qualifiers shared by the controlled question grammar."""
from __future__ import annotations

import re
from typing import Literal

Adjustment = Literal["seasonal", "calendar", "seasonal_and_calendar", "unadjusted", "other"]
Polarity = Literal["affirmative", "negative"]

_JOIN = r"(?:and|or|plus|y|e|o|mas|ademas de|junto con)"
_COORDINATOR = re.compile(rf"\s*,\s*(?:{_JOIN}\s+)?|\s+{_JOIN}\s+")
_COUNTERFACTUAL = re.compile(
    r"\b(?:counterfactual|contrafactual|but for|in the absence of|en ausencia de|de no haber|"
    r"without (?:the |a )?(?:pandemic|war|invasion|covid(?:-19)?|lockdowns?|generative ai)|"
    r"sin (?:la |el )?(?:pandemia|guerra|invasion|covid(?:-19)?|confinamientos?|ia generativa)|"
    r"if .{0,100}?\b(?:had not|had never|hadn't)|si .{0,100}?\b(?:no hubiera|no hubiese)|"
    r"had .{1,100}?\b(?:not|never) (?:occurred|happened|taken place)|had there been no|"
    r"would (?:.{1,80}? )?have (?:been|grown|fallen|changed|increased|decreased)|"
    r"habria (?:sido|crecido|caido|cambiado|aumentado|disminuido))\b",
)
_NEGATIVE = re.compile(
    r"\b(?:not|never|non.use|non.users?|non.adopt\w*|without|no\s+\w+|nunca|jamas|sin utilizar|sin usar|no usuarios?)\b|n['’]t\b",
)
_EXCLUSIVE = re.compile(
    r"\b(?:only|solely|exclusively|alone|restricted to|limited to|"
    r"solo|solamente|unicamente|exclusivamente|restringid[oa]s? a|limitad[oa]s? a)\b",
)
_UNADJUSTED = re.compile(
    r"\b(?:raw|unadjusted|non[- ](?:seasonally[- ])?adjusted|not (?:seasonally[- ])?adjusted|"
    r"without (?:any |the )?(?:seasonal[- ])?adjustments?|"
    r"without (?:adjusting|correcting) for seasonality|"
    r"(?:before|prior to|pre[- ])\s*(?:any |the |applying (?:the )?)?(?:seasonal[- ])?adjustments?|"
    r"before (?:being|it (?:was|is)|(?:the )?data (?:were|are)) seasonally adjusted|"
    r"(?:before|prior to|without) (?:deseasonali[sz]ing|seasonally (?:adjusting|correcting)|"
    r"(?:adjusting|correcting) for (?:seasonality|seasonal (?:effects?|variation))|"
    r"removing (?:seasonality|seasonal (?:effects?|variation)))|"
    r"no (?:desestacionalizad[oa]s?|ajustad[oa]s?(?: (?:por estacionalidad|estacionalmente))?)|"
    r"sin (?:aplicar (?:el )?)?(?:ajustes?(?: estacional(?:es)?)?|desestacionalizar|desestacionalizacion)|"
    r"(?:antes del?|previ[oa] al?) (?:aplicar (?:el )?|el |la )?"
    r"(?:ajuste estacional|desestacionalizacion|desestacionalizar|ajustar estacionalmente)|"
    r"(?:antes de|sin|previo a) (?:ajustar|corregir|eliminar|quitar) "
    r"(?:(?:por )?(?:el |la )?(?:efectos? estacional(?:es)?|estacionalidad)|estacionalmente)|"
    r"en bruto|datos brut[oa]s?|serie brut[oa])\b",
)
_CALENDAR_NEGATION = re.compile(
    r"\b(?:(?:not|non[- ])\s*calendar[- ]adjusted|(?:before|prior to|without) (?:the )?calendar adjustment|"
    r"sin ajuste (?:de |del )?calendario|antes del ajuste (?:de |del )?calendario)\b",
)
_DIRECTIONAL_ADJUSTMENT = re.compile(
    r"\b(?:before|prior to|without|antes del?|previ[oa] al?|sin)\b[^,;?.]{0,55}"
    r"\b(?:seasonal\w*|calendar[- ]adjust\w*|desestacionaliz\w*|estacional\w*)\b",
)
_ADJUSTMENT = re.compile(
    r"\b(?:adjusted|adjustments?|corrected|correction for|"
    r"ajustad[oa]s?|ajustes?|corregid[oa]s?|desestacionaliz\w*)\b",
)
_SEASONAL = re.compile(r"\b(?:seasonal\w*|estacional\w*|desestacionaliz\w*)\b")
_CALENDAR = re.compile(r"\b(?:calendar|calendario|working days?|dias habiles)\b")
_TIME_LEFT = re.compile(
    r"\b(?:before|after|since|until|by|prior to|following|up to|as early as|as late as|"
    r"no later than|no earlier than|earlier than|later than|on or before|on or after|"
    r"early|late|earlier|later|mid|at the start of|at the beginning of|at the end of|around|approximately|towards?|"
    r"antes (?:de|del)|despues (?:de|del)|a partir (?:de|del)|desde|hasta|"
    r"previo a|posterior a|a principios (?:de|del)|a finales (?:de|del)|"
    r"no mas tarde (?:de|del)|no antes (?:de|del)|alrededor (?:de|del)|"
    r"a mas tardar|hacia)"
    r"(?:[- ]+(?:in|en|of|de|del|the|el|la|start|beginning|inicio|end|fin|ending|lockdown|confinamiento)){0,5}[- ]*$",
)
_TIME_RIGHT = re.compile(r"^\s+(?:or (?:earlier|later|before|after)|onward[s]?|en adelante|o (?:antes|despues))\b")


def operands(text: str) -> tuple[str, ...]:
    return tuple(part.strip(" ,") for part in _COORDINATOR.split(text.strip(" ,")) if part.strip(" ,"))


def counterfactual(text: str) -> bool:
    return _COUNTERFACTUAL.search(text) is not None


def polarity(text: str) -> Polarity:
    text = _UNADJUSTED.sub("", _CALENDAR_NEGATION.sub("", text))
    return "negative" if _NEGATIVE.search(text) else "affirmative"


def exclusive(text: str) -> bool:
    return _EXCLUSIVE.search(text) is not None


def adjustment(text: str) -> Adjustment | None:
    if _CALENDAR_NEGATION.search(text):
        return "other"
    unadjusted = _UNADJUSTED.search(text) is not None
    text = _UNADJUSTED.sub("", text)
    if _DIRECTIONAL_ADJUSTMENT.search(text):
        return "other"
    if not _ADJUSTMENT.search(text):
        return "unadjusted" if unadjusted else None
    if unadjusted:
        return "other"
    seasonal = _SEASONAL.search(text) is not None
    calendar = _CALENDAR.search(text) is not None
    if seasonal and calendar:
        return "seasonal_and_calendar"
    if seasonal:
        return "seasonal"
    if calendar:
        return "calendar"
    return "other"


def relative_time(text: str, start: int, end: int) -> bool:
    return _TIME_LEFT.search(text[:start]) is not None or _TIME_RIGHT.search(text[end:]) is not None
