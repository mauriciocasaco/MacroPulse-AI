"""Structured interpretation providers with a fixed synthetic Gemini smoke input."""

from dataclasses import dataclass
from http.client import HTTPException
import json
import os
from typing import Literal, Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field


Relevance = Literal["direct", "contextual"]
Coverage = Literal["sufficient", "partial", "insufficient"]
MAX_CITATIONS = 3
MAX_RESPONSE_BYTES = 16_384
GEMINI_MODEL = "gemini-3.6-flash"


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    relevance: Relevance
    mechanism: str


class CitationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    coverage: Coverage
    citation_ids: list[str] = Field(max_length=MAX_CITATIONS)


class EconomicMechanism(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    citation_id: str
    summary: str = Field(min_length=1, max_length=600)


class Interpretation(CitationSelection):
    mechanisms: list[EconomicMechanism] = Field(max_length=MAX_CITATIONS)


class ProviderError(RuntimeError):
    """The provider failed or returned an unsupported interpretation."""


class InterpretationProvider(Protocol):
    def generate(self, evidence: tuple[EvidenceRef, ...]) -> Interpretation: ...


def coverage_of(evidence: tuple[EvidenceRef, ...]) -> Coverage:
    if any(item.relevance == "direct" for item in evidence):
        return "sufficient"
    return "partial" if evidence else "insufficient"


class SimulatedProvider:
    def generate(self, evidence: tuple[EvidenceRef, ...]) -> Interpretation:
        return Interpretation(coverage=coverage_of(evidence), citation_ids=[item.id for item in evidence],
                              mechanisms=_mechanisms(evidence))


def _mechanisms(evidence: tuple[EvidenceRef, ...]) -> list[EconomicMechanism]:
    return [EconomicMechanism(citation_id=item.id, summary=item.mechanism) for item in evidence]


def validate_interpretation(result: Interpretation, evidence: tuple[EvidenceRef, ...]) -> Interpretation:
    if not isinstance(result, Interpretation):
        raise ProviderError("Invalid interpretation")
    by_id = {item.id: item for item in evidence}
    ids = result.citation_ids
    if len(ids) > MAX_CITATIONS or len(set(ids)) != len(ids) or any(id_ not in by_id for id_ in ids):
        raise ProviderError("Invalid interpretation citations")
    cited = tuple(by_id[id_] for id_ in ids)
    if result.coverage != coverage_of(cited) or result.coverage != coverage_of(evidence):
        raise ProviderError("Invalid interpretation coverage")
    if result.mechanisms != _mechanisms(cited):
        raise ProviderError("Invalid interpretation mechanisms")
    return result


SYNTHETIC_SMOKE = (EvidenceRef("synthetic:1", "direct", "The fictional exchange notice changes the settlement schedule."),)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GeminiProvider:
    """Only the fixed synthetic fixture can cross the external-provider boundary."""

    def __init__(self, *, free_tier_verified: bool):
        if free_tier_verified is not True:
            raise ProviderError("Gemini free tier has not been verified for this project")

    def generate(self, evidence: tuple[EvidenceRef, ...]) -> Interpretation:
        if evidence is not SYNTHETIC_SMOKE:
            raise ProviderError("Evidence is ineligible for Gemini")
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("Gemini credential unavailable")
        payload = {
            "systemInstruction": {"parts": [{"text": (
                "Classify coverage and cite IDs from the supplied synthetic fixture. "
                "A direct source gives sufficient coverage. Use English and return the requested JSON only."
            )}]},
            "contents": [{"parts": [{"text": (
                "Synthetic fixture. A fictional exchange notice, source ID synthetic:1, "
                "directly documents the planned settlement schedule change in a fictional contract."
            )}]}],
            "generationConfig": {
                "maxOutputTokens": 128,
                "thinkingConfig": {"thinkingLevel": "MINIMAL"},
                "responseMimeType": "application/json",
                "responseJsonSchema": CitationSelection.model_json_schema(),
            },
        }
        request = Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            opener = build_opener(ProxyHandler({}), _NoRedirects())
            with opener.open(request, timeout=8) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error.close()
            raise ProviderError("Gemini request failed") from None
        except (HTTPException, OSError, ValueError):
            raise ProviderError("Gemini request failed") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("Gemini response exceeded the size limit")
        try:
            candidate, = json.loads(raw)["candidates"]
            part, = candidate["content"]["parts"]
            if candidate["finishReason"] != "STOP" or part.get("thought"):
                raise ValueError("incomplete response")
            selection = CitationSelection.model_validate_json(part["text"])
        except (AttributeError, KeyError, RecursionError, TypeError, ValueError):
            raise ProviderError("Gemini returned an invalid response") from None
        result = Interpretation(**selection.model_dump(), mechanisms=_mechanisms(evidence))
        return validate_interpretation(result, evidence)
