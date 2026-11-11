"""Strict public M7.B exchange schemas."""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def valid_day(value):
    date.fromisoformat(value)
    return value


def valid_instant(value):
    datetime.fromisoformat(value)
    return value

ID = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(valid_day)]
Text = Annotated[str, Field(min_length=1, max_length=5000)]
URL = Annotated[str, Field(pattern=r"^https://[^\s]+$", max_length=2048)]
Event = Literal["covid", "ukraine", "genai"]
Indicator = Literal["eur-usd", "m3-stock", "hicp-yoy", "real-gdp-qoq", "deposit-rate"]
Relation = Literal["direct", "context", "proxy"]
Institution = Literal["BCE", "Eurostat", "BIS", "Reserva Federal (Junta)"]
Status = Literal["approved", "rejected", "needs_review"]
ClaimType = Literal["observed", "institutional_interpretation", "projection_or_expectation", "unconfirmed_hypothesis"]
DateTime = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$"), AfterValidator(valid_instant)]
Notice = Literal["restrictive_notice", "named_authorship", "copyright_or_credit"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class HistoricalPeriod(Strict):
    start: Day
    end: Day

    @model_validator(mode="after")
    def chronological(self):
        if self.start > self.end:
            raise ValueError("inverted historical period")
        return self


class Summary(Strict):
    observed: Text
    institutional_interpretation: Text | None
    projection_or_expectation: Text | None
    unconfirmed_hypothesis: Text | None
    limitations: Text

    @model_validator(mode="after")
    def bounded(self):
        count = sum(len(value.split()) for value in self.model_dump().values() if value)
        if not 100 <= count <= 200:
            raise ValueError("summary must contain 100–200 words across its typed sections")
        return self


class Rights(Strict):
    policy_url: URL
    policy_sha256: ID
    basis: Text
    specific_evidence: Text
    attribution: Text
    redistribution_scope: Literal["attributed_paraphrases_and_structured_facts"]
    originals_allowed_in_corpus: Literal[False]
    original_text_chunks_authorized: Literal[False]


class Decision(Strict):
    schema_version: Literal["m7b-v1"]
    document_id: ID
    event: Event
    institution: Institution
    original_url: URL
    m7a_publication_date: Day
    m7a_content_sha256: ID
    status: Status
    is_original_proposal: bool
    replaces: ID | None
    checked_at: DateTime
    source_access: Literal["accessible", "unverified"]
    source_identity: Literal["verified", "unverified"]
    title: Text
    document_type: Text | None
    source_payload_sha256: ID | None
    normalized_text_sha256: ID | None
    source_change: Literal["same", "changed", "not_checked"]
    notice_flags: list[Notice]
    content_review: Text
    content_locator: Text | None
    rights_status: Literal["scoped_derivatives_verified", "restricted", "not_cleared", "not_needed"]
    rights_evidence: Text
    rights_url: URL
    reason: Text


class Document(Strict):
    schema_version: Literal["m7b-v1"]
    id: ID
    m7a_id: ID
    title: Text
    institution: Institution
    primary_source: URL
    media_type: Literal["text/html", "application/pdf"]
    document_type: Text
    publication_date: Day
    date_precision: Literal["day"]
    reviewed_at: DateTime
    language: Literal["en"]
    event: Event
    geography: Text
    historical_period: HistoricalPeriod
    indicators: dict[Indicator, Relation]
    mechanism: Text
    summary: Summary
    rights: Rights
    source_payload_sha256: ID
    normalized_text_sha256: ID
    claim_ids: Annotated[list[ID], Field(min_length=1, max_length=20)]


class Claim(Strict):
    schema_version: Literal["m7b-v1"]
    id: ID
    document_id: ID
    text: Text
    type: ClaimType
    primary_source: URL
    locator: Text
    source_block_sha256: ID
    publication_date: Day
    historical_period: HistoricalPeriod
    geography: Text
    event: Event
    indicators: Annotated[list[Indicator], Field(min_length=1, max_length=5)]
    qualifiers: Text
    review_status: Literal["verified_against_source"]


class DraftClaim(Strict):
    text: Text
    type: ClaimType
    locator: Text
    source_block_sha256: ID
    indicators: Annotated[list[Indicator], Field(min_length=1, max_length=5)]
    qualifiers: Text
    historical_period: HistoricalPeriod | None = None


class Review(Strict):
    status: Status
    replaces: ID | None = None
    reason: Text
    content_review: Text
    content_locator: Text | None = None
    rights_status: Literal["scoped_derivatives_verified", "restricted", "not_cleared", "not_needed"]
    rights_evidence: Text
    geography: Text | None = None
    historical_period: HistoricalPeriod | None = None
    indicators: dict[Indicator, Relation] | None = None
    mechanism: Text | None = None
    summary: Summary | None = None
    claims: list[DraftClaim] | None = None
    reviewed_payload_sha256: ID | None = None
    reviewed_text_sha256: ID | None = None
    reviewed_policy_sha256: ID | None = None


class SourceCheck(Strict):
    id: ID
    url: URL
    institution: Institution
    access: Literal["accessible", "unverified"]
    identity: Literal["verified", "unverified"]
    checked_at: DateTime
    final_url: URL | None = None
    media_type: Literal["text/html", "application/pdf"] | None = None
    redirects: list[URL] | None = None
    payload_sha256: ID | None = None
    title: Text | None = None
    page_date: Day | None = None
    kind: Text | None = None
    notices: list[Notice] | None = None
    text_sha256: ID | None = None
    comparison_sha256: ID | None = None
    document_reproduction_notice: bool | None = None
    blocks: dict[ID, ID] = Field(default_factory=dict)
    error: Text | None = None


class PolicyCheck(Strict):
    url: URL
    verified: bool
    sha256: ID | None = None
    basis: Text | None = None
    error: Text | None = None


class Snapshot(Strict):
    as_of: DateTime
    requests: Annotated[int, Field(ge=0, le=310)]
    policies: dict[Institution, PolicyCheck]
    documents: dict[ID, SourceCheck]
    m7a_sha256: dict[Literal["manifest.json", "candidates.jsonl"], ID] | None = None
