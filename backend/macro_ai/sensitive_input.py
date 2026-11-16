"""High-confidence patterns for research admission and Gemini egress."""

from collections.abc import Iterable
from contextvars import ContextVar
import re
import unicodedata


configured_sensitive_values: ContextVar[tuple[str, ...]] = ContextVar("configured_sensitive_values", default=())

SENSITIVE_CODE = "research_sensitive_information"
SENSITIVE_MESSAGE = "Can't process a question containing sensitive information."


class SensitiveInputError(ValueError):
    """A sensitive value was detected. The value must never enter the error."""

    def __init__(self):
        super().__init__(SENSITIVE_MESSAGE)


_CREDENTIAL = re.compile(
    r"\b(?:AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{32,}|"
    r"(?:sk-|sb_secret_|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b"
    r"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r"|\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"
    r"|\bbearer[ \t]+[a-z0-9._~+/=-]{12,}"
    r"|\b(?:proxy[-_ ]?)?authorization[\"']?\s*[:=]\s*basic[ \t]+[a-z0-9+/=]{8,}"
    r"|\b(?:api[_ -]?key|password|passwd|secret|(?:access[_ -]?|refresh[_ -]?|auth[_ -]?)?token|"
    r"(?:proxy[-_ ]?)?authorization)[\"']?[ \t]*[:=][ \t]*[\"']?[^\s\"',;]+"
    r"|\bmy\s+(?:password|api[_ -]?key|access[_ -]?token)\s+is\s+[\"']?[^\s\"',;]+",
    re.IGNORECASE | re.ASCII,
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^_~-]+@(?:[\w-]+\.)+[a-z]{2,63}\b", re.IGNORECASE)
_PHONE = re.compile(
    r"(?<![\w\d])(?:"
    r"(?:\+[1-9][0-9]{0,2}[ .-]?)?\([0-9]{2,4}\)[ .-]?[0-9]{3,4}[ .-][0-9]{4}"
    r"|[0-9]{3}[.-][0-9]{3}[.-][0-9]{4}"
    r"|\+[1-9][0-9]{0,2}(?:[ .-][0-9]{2,4}){1,3}[ .-][0-9]{3,4}"
    r"|\b(?:phone|tel(?:ephone)?|mobile)\s*[:=]?\s*\+[1-9][0-9]{7,14})(?![\w/%-]|\.[0-9])", re.I,
)
_LABELED_ID = re.compile(r"\b(?:ssn|social security number)\s*[:=]?\s*[0-9]{3}-[0-9]{2}-[0-9]{4}\b", re.I)
_CARD = re.compile(
    r"(?<![\d.,])(?:[0-9]{4}([ -])[0-9]{4}\1[0-9]{4}\1[0-9]{4}|"
    r"(?:credit card|debit card|card number)\s*[:=]?\s*[0-9]{13,19})(?![\w%]|[.,][0-9])", re.I,
)
_IBAN = re.compile(r"\b(?:IBAN\s*[:=]?\s*)?([A-Z]{2}[0-9]{2}(?:[A-Z0-9]{11,30}|(?: [A-Z0-9]{4}){2,7} [A-Z0-9]{1,4}))\b")


def _normalized(text: str) -> str:
    return unicodedata.normalize(
        "NFKC", "".join(char for char in text if unicodedata.category(char) != "Cf"),
    )


def _valid_card(text: str) -> bool:
    digits = [int(char) for char in text if char in "0123456789"]
    if len(set(digits)) == 1:
        return False
    doubled = (2 * digit for digit in digits[-2::-2])
    return (sum(digits[::-2]) + sum(value - 9 if value > 9 else value for value in doubled)) % 10 == 0


def _valid_iban(text: str) -> bool:
    code = text.replace(" ", "")
    if not 15 <= len(code) <= 34:
        return False
    digits = "".join(char if char.isdigit() else str(ord(char) - ord("A") + 10)
                     for char in code[4:] + code[:4])
    return int(digits) % 97 == 1


def contains_sensitive_input(text: str, *, secrets: Iterable[str] = ()) -> bool:
    normalized = _normalized(text)
    for secret in secrets:
        if secret and (secret in text or ((value := _normalized(secret)) and value in normalized)):
            return True
    return (
        any(pattern.search(normalized) is not None for pattern in (_CREDENTIAL, _EMAIL, _PHONE, _LABELED_ID))
        or any(_valid_card(match[0]) for match in _CARD.finditer(normalized))
        or any(_valid_iban(match[1]) for match in _IBAN.finditer(normalized.upper()))
    )
