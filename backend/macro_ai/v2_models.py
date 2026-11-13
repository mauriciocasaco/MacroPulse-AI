"""Research v2 contracts. Only catalogued series can become tool inputs."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..macro_data.api import MacroWindow
from .models import Claim, EventId, Evidence, IndicatorId, ResearchResponse


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(StrictOutput):
    excerpt: str = Field(min_length=1, max_length=500, description="Verbatim excerpt of the question")
    indicator: IndicatorId | None = None
    geography: str | None = Field(default=None, max_length=80)
    measure: str | None = Field(default=None, max_length=80)
    operation: Literal["trend", "difference", "relative_change", "point", "causal_effect", "other"]
    period: MacroWindow | None = None
    operands: list[MacroWindow] = Field(default_factory=list, max_length=2)
    unit: str | None = Field(default=None, max_length=80)
    adjustment: str | None = Field(default=None, max_length=80)
    variant: str | None = Field(default=None, max_length=80)
    population: str | None = Field(default=None, max_length=100)
    subgroup: str | None = Field(default=None, max_length=100)
    other_constraints: list[str] = Field(default_factory=list, max_length=8)


class Intent(StrictOutput):
    event: EventId | None = None
    purpose: Literal["descriptive", "causal", "uncertain"]
    requirements: list[Requirement] = Field(min_length=1, max_length=4)
    unresolved: list[str] = Field(default_factory=list, max_length=8)


MAX_NARRATIVE_STATEMENTS = 16
NarrativeStyle = Literal["direct", "framed"]


class DraftParagraph(StrictOutput):
    statement_id: StrictInt = Field(ge=1, le=MAX_NARRATIVE_STATEMENTS)
    style: NarrativeStyle


class Draft(StrictOutput):
    paragraphs: list[DraftParagraph] = Field(min_length=1, max_length=MAX_NARRATIVE_STATEMENTS)


class SeriesEvidence(Evidence):
    kind: Literal["series"]
    series_key: str
    unit: str
    multiplier: int
    geography: str
    adjustment: str


class CuratedEvidence(Evidence):
    kind: Literal["document"]
    claim_id: str
    document_id: str
    event: str
    indicator: IndicatorId
    geography: str
    relation: Literal["direct", "context", "proxy"]
    locator: str
    qualifiers: str
    claim_type: str


Category = Literal["official observation", "calculated from official observations",
                   "documentary context", "institutional interpretation", "proxy context"]


class NarrativeClaim(Claim):
    category: Category


class ResearchV2Response(ResearchResponse):
    research_v2: Literal[True] = True
    support: Literal["supported", "partial", "insufficient"]
    publication_cutoff: date
    corpus_sha256: str | None = None
    evidence: list[SeriesEvidence | CuratedEvidence]
    narrative: list[NarrativeClaim]
