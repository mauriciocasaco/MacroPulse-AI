"""Research v2 contracts. Only catalogued series can become tool inputs."""

from datetime import date, datetime
import re
from typing import Literal
from unicodedata import normalize

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

# Only known names can contain digits. Keep this policy in sync with macro-api.ts.
_NUMBERED_NAMES = re.compile(
    r"\b(?:M[0-4]|COVID[\s\-\u2010-\u2013\u2212]?19|G7|G20|EA(?:19|20|21)|EU-?27)\b", re.IGNORECASE,
)
_INSTITUTION = r"(?:ECB|Eurostat|European Central Bank)"
_SOURCE_WORD = r"(?:data|figures|statistics|report|study|release|bulletin|analysis|estimates)"
_REPORTING_VERB = (
    r"(?:says?|said|shows?|showed|shown|finds?|found|reports?|reported|confirms?|confirmed|"
    r"publishes?|published|documents?|documented|estimates?|estimated|concludes?|concluded|"
    r"indicates?|indicated|states?|stated|suggests?|suggested|reveals?|revealed|claims?|claimed)"
)
_INSTITUTIONAL_ATTRIBUTION = re.compile(
    rf"\b{_INSTITUTION}(?:['’]s)?\s+(?:(?:\w+\s+){{0,3}}{_SOURCE_WORD}\s+)?"
    rf"(?:(?:has|have|had|also|previously|officially|recently)\s+)*{_REPORTING_VERB}\b|"
    rf"\b{_REPORTING_VERB}\s+by\s+(?:the\s+)?{_INSTITUTION}\b|"
    rf"\b{_SOURCE_WORD}\s+(?:from|by)\s+(?:the\s+)?{_INSTITUTION}\s+{_REPORTING_VERB}\b|"
    rf"\b(?:per|as cited by)\s+(?:the\s+)?{_INSTITUTION}\b|"
    rf"\(\s*(?:the\s+)?{_INSTITUTION}\s*\)",
    re.IGNORECASE,
)
# These checks catch recognizable figures and references. Free text cannot be
# fact-checked by a pattern, so the response remains explicitly unverified.
_NON_CONCEPTUAL = re.compile(
    r"[%<>\[\]]|https?\s*:|ftp\s*:|www\.|\b[\w-]+\.(?:com|org|net|gov|edu|eu|int|test|example)\b|"
    r"\b[\w-]+\.[a-z]{2,63}/|"
    r"\b(?:sources?|references?|citations?|bibliography)\s*:|\b(?:according to|DOI)\b|"
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"\w+teen|\w+ty|hundred|thousand|million|billion|trillion|half|quarter)[\s\-]+"
    r"(?:per\s+cent|percent(?:age\s+points?)?|basis\s+points?|"
    r"hundred|thousand|million|billion|trillion|euros?|dollars?|pounds?)\b",
    re.IGNORECASE,
)


class GeneralDraft(StrictOutput):
    answer: str = Field(min_length=20, max_length=2400)

    @field_validator("answer", mode="before")
    @classmethod
    def conceptual_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            checked = normalize("NFKC", value)
            if (
                _NON_CONCEPTUAL.search(checked) or _INSTITUTIONAL_ATTRIBUTION.search(checked)
                or any(char.isnumeric() for char in _NUMBERED_NAMES.sub(" ", checked))
                or any(not char.isprintable() and char != "\n" for char in value)
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
