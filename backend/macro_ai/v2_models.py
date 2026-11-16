"""Research v2 contracts. Only catalogued series can become tool inputs."""

from datetime import date, datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, computed_field, field_validator

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

CATALOG_VERSION = "v1"
CONCEPTUAL_FRAGMENTS: dict[str, str] = json.loads(
    Path(__file__).with_name("conceptual_catalog.json").read_text(encoding="utf-8")
)[CATALOG_VERSION]


class GeneralDraft(StrictOutput):
    fragment_ids: list[StrictStr] = Field(min_length=1, max_length=2)

    @field_validator("fragment_ids")
    @classmethod
    def known_unique_fragments(cls, ids: list[str]) -> list[str]:
        if len(set(ids)) != len(ids) or any(fragment not in CONCEPTUAL_FRAGMENTS for fragment in ids):
            raise ValueError("Expected unique conceptual fragment identifiers")
        return ids


class GeneralFallbackBase(StrictOutput):
    triggered: Literal[True] = True
    reason: Literal["unsupported_operation_or_series"]
    model_id: FallbackModelId
    verified: Literal[False] = False


class GeneralFallbackAnswer(GeneralDraft, GeneralFallbackBase):
    status: Literal["answered"] = "answered"
    catalog_version: Literal["v1"] = CATALOG_VERSION

    @computed_field
    @property
    def answer(self) -> str:
        return "\n\n".join(CONCEPTUAL_FRAGMENTS[fragment] for fragment in self.fragment_ids)


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
