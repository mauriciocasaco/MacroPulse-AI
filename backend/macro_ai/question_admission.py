"""Bounded language accepted by the attested public Research API."""

from dataclasses import dataclass
import re
from typing import Literal
import unicodedata

POLICY_REJECTION = "research_question_not_admitted"

QuestionKind = Literal[
    "descriptive", "conceptual", "clarification", "out_of_domain", "agent_control", "sensitive", "invalid",
]


@dataclass(frozen=True)
class QuestionAdmission:
    kind: QuestionKind
    fragment_ids: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.kind in {"descriptive", "conceptual"}


# These patterns help identify categories for recognized controls and secrets.
# Positive admission below applies even when neither pattern matches.
_SENSITIVE = re.compile(
    r"aiza[\w-]{20,}|\baq\.[\w-]{32,}|(?:postgres(?:ql)?|https?)://[^\s/@]+:[^\s/@]+@|"
    r"\b(?:sk-|sb_secret_|gh[pousr]_|github_pat_)[\w-]{20,}|"
    r"\b(?:api[_ -]?key|password|secret|access[_ -]?token|authorization)\s*[:=]|"
    r"\bbearer\s+[\w.+/=-]{12,}|-----begin [a-z ]*private key-----"
)
_CONTROL = re.compile(
    r"\b(?:ignore|disregard|override|bypass|obey|disobey|jailbreak|developer|instructions?|"
    r"prompts?|policies|tool|reveal|disclose|pretend|payload|decode|ignora|instrucciones|"
    r"divulga|revela|desarrollador|herramienta)\b"
)
_ALPHABET = re.compile(r"[a-záéíóúüñ0-9 ¿?.,/%-]+")
_DEFINITION = re.compile(
    r"(?:what (?:is|are)|define|explain|qué (?:es|son)|explica)"
    r" (?:the |an? |el |la |los |las )?(.+)"
)
_MEANING = re.compile(r"what does (.+) mean")
_TOPICS = {
    ("inflation", "inflación"): ("inflation", "inflation-channels"),
    ("monetary policy", "política monetaria"): ("monetary-policy", "monetary-transmission"),
    ("exchange rate", "exchange rates", "tipo de cambio"): ("exchange-rate", "exchange-rate-channels"),
    ("ecb deposit facility", "ecb deposit facility rate", "ecb deposit rate",
     "facilidad de depósito del bce", "tasa de depósito del bce"): (
        "deposit-facility", "deposit-facility-transmission",
    ),
    ("m3", "broad money", "dinero amplio", "agregado monetario m3"): ("m3", "m3-channels"),
    ("real gdp", "gdp", "pib real", "pib"): ("gdp", "gdp-productivity"),
}
_DEFINITIONS = {alias: fragments for aliases, fragments in _TOPICS.items() for alias in aliases}
_MECHANISMS = {
    "what causes inflation": ("inflation-channels",),
    "how does monetary policy work": ("monetary-policy", "monetary-transmission"),
    "how do interest rates affect inflation": ("monetary-policy-inflation",),
    "how does monetary policy affect inflation": ("monetary-policy-inflation",),
    "cómo afecta la política monetaria a la inflación": ("monetary-policy-inflation",),
    "how does the exchange rate affect inflation": ("exchange-rate-inflation",),
    "how do exchange rates affect inflation": ("exchange-rate-inflation",),
    "how does bank lending affect m3": ("m3-channels",),
    "how does productivity affect real gdp": ("gdp-productivity",),
}
_DESCRIPTIVE_START = re.compile(
    r"^(?:how (?:did|much|many|was|has|have|would)|what (?:happened|was|were)|"
    r"describe|compare|show|calculate|cuánto|cómo|compara|describe)\b"
)
_DOMAIN = frozenset(
    "inflation hicp cpi eur/usd gdp m3 ecb bce pib inflación harmonised unemployment exchange eurozone "
    "deposit refinancing rates".split()
)
# Vocabulary for observations, operations, dates, scopes and known events.
# An unknown token in any clause rejects the whole question.
_WORDS = _DOMAIN | frozenset("""
    how did much many was has have would what happened were describe compare show calculate cuánto cómo compara
    the a an it to in of from for with as on at by between and or than during since until over before after
    change changes changed evolve evolved move moved grow grew growth increase increased decrease decreased rise
    rose decline fell fall relate related affect affected impact effect difference trend trends evolution level
    levels end start average value rate rates index stock money output real nominal raw gross domestic product
    dollar euro euros bilateral currency currencies foreign spot reference broad monetary policy interest
    annual monthly daily quarterly headline core harmonized consumer price prices adjusted unadjusted
    seasonally seasonal adjustment percentage points percent basis units eurozone euro-area euro area
    europe european union us united states germany france spain national regional household households
    deposits deposit facility refinancing mro lending official observations measure adjusted published latest
    pandemic covid covid-19 coronavirus shock war ukraine invasion rise generative ai and llms technology boom
    actual counterfactual forecast expected current selected period window average exporters imports exports
    including excluding without if preliminary final revised revision first second third fourth quarter month
    year annualised annualized qoq yoy change jan january feb february mar march apr april may jun june jul
    july aug august sep september oct october nov november dec december q1 q2 q3 q4
    el la las los del en de y a entre desde hasta durante cambió evolucionó subió bajó fue eran tipo cambio
    tasa tasas depósito facilidad real anual mensual trimestral interanual porcentual porcentuales puntos
    zona guerra ucrania pandemia período enero febrero marzo abril mayo junio julio agosto septiembre octubre
    noviembre diciembre
""".split())
_DATE_OR_PERIOD = re.compile(r"(?:[1-9]|[12][0-9]|3[01]|(?:19|20)[0-9]{2}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12][0-9]|3[01]))?|-q[1-4])?)")
_UNSPECIFIED = frozenset({"how did it change", "what happened", "what was the change", "describe the change"})


def _conceptual_ids(text: str) -> tuple[str, ...]:
    if text in _MECHANISMS:
        return _MECHANISMS[text]
    definition = _MEANING.fullmatch(text) or _DEFINITION.fullmatch(text)
    return _DEFINITIONS.get(definition[1], ()) if definition else ()


def classify_question(question: str) -> QuestionAdmission:
    if not 5 <= len(question) <= 500 or any(ord(char) < 32 for char in question):
        return QuestionAdmission("invalid")

    folded = unicodedata.normalize("NFKC", question).casefold()
    recognizable = "".join(char for char in folded if unicodedata.category(char) != "Cf")
    if _SENSITIVE.search(recognizable):
        return QuestionAdmission("sensitive")
    if _CONTROL.search(recognizable):
        return QuestionAdmission("agent_control")

    text = unicodedata.normalize("NFC", question).lower()
    if not _ALPHABET.fullmatch(text):
        return QuestionAdmission("invalid")
    text = " ".join(text.removeprefix("¿").removesuffix("?").removesuffix(".").split())
    if text in _UNSPECIFIED:
        return QuestionAdmission("clarification")
    if fragments := _conceptual_ids(text):
        return QuestionAdmission("conceptual", fragments)

    words = text.replace(",", " ").split()
    if not _DOMAIN.intersection(words):
        return QuestionAdmission("out_of_domain")
    if (_DESCRIPTIVE_START.match(text) and len(words) <= 48
            and all(word in _WORDS or _DATE_OR_PERIOD.fullmatch(word) for word in words)):
        return QuestionAdmission("descriptive")
    return QuestionAdmission("clarification")
