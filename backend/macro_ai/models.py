"""Strict request, evidence and response contracts for local macro research."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..macro_data.api import MacroWindow


class ResearchRequest(MacroWindow):
    question: str = Field(min_length=5, max_length=500)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5 or any(ord(char) < 32 for char in value):
            raise ValueError("invalid research question")
        return value


class Evidence(BaseModel):
    id: str
    kind: Literal["series", "document"]
    title: str
    url: str
    attribution: str
    period_start: date | None = None
    period_end: date | None = None
    published_on: date | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=10, max_length=600)
    citations: list[str] = Field(min_length=1, max_length=4)


class GeminiDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interpretation: list[Claim] = Field(min_length=1, max_length=2)


class ResearchResponse(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    question: str
    start: date
    end: date
    facts: list[Claim] = Field(default_factory=list)
    calculations: list[Claim] = Field(default_factory=list)
    interpretation: list[Claim] = Field(default_factory=list)
    limitations: list[str]
    evidence: list[Evidence]
    graph_steps: list[str]


def selected_citations(output: object, evidence: list[Evidence]) -> list[tuple[str, ...]]:
    """Return only validated source selections from the untrusted draft."""
    draft = output if isinstance(output, GeminiDraft) else GeminiDraft.model_validate(output)
    kinds = {entry.id: entry.kind for entry in evidence}
    selections = []
    for claim in draft.interpretation:
        if (len(set(claim.citations)) != len(claim.citations)
                or any(citation not in kinds for citation in claim.citations)
                or {kinds[citation] for citation in claim.citations} != {"series", "document"}):
            raise ValueError("invalid model evidence references")
        selections.append(tuple(claim.citations))
    return selections
