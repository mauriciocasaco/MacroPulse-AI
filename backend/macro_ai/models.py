"""Strict request, evidence and response contracts for local macro research."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from ..macro_data.api import MacroWindow


EVENT_WINDOWS = {
    "covid": (date(2020, 2, 1), date(2020, 12, 31)),
    "ukraine": (date(2022, 2, 1), date(2024, 2, 29)),
    "generative-ai": (date(2023, 9, 1), date(2026, 9, 30)),
}
IndicatorId = Literal["eur-usd", "m3-stock", "deposit-rate", "real-gdp-qoq", "hicp-yoy"]
EventId = Literal["covid", "ukraine", "generative-ai"]


class ResearchRequest(MacroWindow):
    question: str = Field(min_length=5, max_length=500)
    indicator_id: IndicatorId | None = None
    event_id: EventId | None = None

    @model_validator(mode="after")
    def selected_window(self):
        if self.event_id and (self.start, self.end) != EVENT_WINDOWS[self.event_id]:
            raise ValueError("selected event requires its exact historical window")
        return self

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
    updated_at: datetime | None = None
    rights_url: str | None = None
    similarity_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    similarity_metric: Literal["cosine"] | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=10, max_length=600)
    citations: list[str] = Field(min_length=1, max_length=4)


class DraftSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: StrictInt
    text: str = Field(min_length=10, max_length=300)


class GeminiDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentences: list[DraftSentence] = Field(min_length=2, max_length=3)
    caveat: str = Field(min_length=10, max_length=200)


class ObservedChange(BaseModel):
    indicator_id: IndicatorId
    value: str = Field(pattern=r"^-?\d+\.\d{2}$")
    unit: Literal["percent", "percentage_points"]
    direction: Literal["up", "down", "flat"]
    first_observed_on: date
    last_observed_on: date
    citation: str


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
    narrative: list[Claim] = Field(default_factory=list)
    narrative_kind: Literal["gemini", "deterministic"] | None = None
    observed_changes: list[ObservedChange] = Field(default_factory=list)
    model_id: str | None = None
    indicator_id: IndicatorId | None = None
    event_id: EventId | None = None
