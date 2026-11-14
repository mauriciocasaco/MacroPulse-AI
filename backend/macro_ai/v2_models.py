"""Research v2 contracts. Only catalogued series can become tool inputs."""

from datetime import date, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

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
    claim_version: str | None = None
    available_at: datetime | None = None
    measure: str | None = None
    unit: str | None = None
    event_linkage: str | None = None
    source_id: str | None = None


Category = Literal["official observation", "calculated from official observations",
                   "documentary context", "institutional interpretation", "proxy context"]


class NarrativeClaim(Claim):
    category: Category


FallbackReason = Literal[
    "ambiguous_interpretation", "unsupported_operation_or_series",
    "missing_official_observations", "insufficient_documentary_evidence", "unsupported_causal_inference",
]
ResearchDiagnostic = FallbackReason | Literal[
    "interpretation_unavailable", "evidence_reader_unavailable", "citation_validation_failed", "no_verified_statements",
]
FallbackModelId = Literal["gemini-3.6-flash", "gemini-3.5-flash-lite"]

# The general view accepts conceptual plain text only. These checks reject
# recognizable figures, links and citations; they do not verify economic claims.
_NON_CONCEPTUAL = re.compile(
    r"\d|[%<>\[\]]|(?:https?\s*:|www\.|\b\w+\.(?:com|org|net|gov|edu|eu|int)\b)|"
    r"\b(?:sources?|references?|citations?)\s*:|"
    r"\b(?:according to|ECB|Eurostat|European Central Bank|DOI)\b",
    re.IGNORECASE,
)


class GeneralDraft(StrictOutput):
    answer: str = Field(min_length=20, max_length=2400)

    @field_validator("answer", mode="before")
    @classmethod
    def conceptual_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if _NON_CONCEPTUAL.search(value) or any(
                not char.isprintable() and char != "\n" for char in value
            ):
                raise ValueError("Expected a conceptual explanation without figures or citations")
        return value


class GeneralFallbackBase(StrictOutput):
    triggered: Literal[True] = True
    reason: FallbackReason
    model_id: FallbackModelId
    verified: Literal[False] = False


class GeneralFallbackAnswer(GeneralDraft, GeneralFallbackBase):
    status: Literal["answered"] = "answered"


class GeneralFallbackFailure(GeneralFallbackBase):
    status: Literal["failed"] = "failed"
    error: Literal["timeout", "provider_unavailable", "invalid_response"]


class ResearchV2Response(ResearchResponse):
    research_v2: Literal[True] = True
    support: Literal["supported", "partial", "insufficient"]
    publication_cutoff: date
    corpus_sha256: str | None = None
    evidence: list[SeriesEvidence | CuratedEvidence]
    narrative: list[NarrativeClaim]
    insufficiency_reason: ResearchDiagnostic | None = Field(default=None, exclude_if=lambda value: value is None)
    general_fallback: GeneralFallbackAnswer | GeneralFallbackFailure | None = Field(
        default=None, discriminator="status", exclude_if=lambda value: value is None,
    )
