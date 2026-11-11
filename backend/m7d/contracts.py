"""Immutable data exchanged by the offline evidence gate and future graph nodes."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from backend.m7c.corpus import Geography, Scope
from backend.macro_data.contracts import CATALOG
from scripts.m7b_schema import ClaimType, Day, Event, ID, Indicator, Institution, Relation, Strict, Text, URL

from .predicates import Measure

Sufficiency = Literal["sufficient", "partial", "insufficient", "unsupported_causal"]
Use = Literal["supports_requested_fact", "context_only"]
Limitation = Literal[
    "unsupported_question", "no_approved_claim_matches", "incomplete_predicate_coverage", "ambiguous_period_or_vintage",
    "causal_identification_unavailable", "official_vintage_unverified_or_out_of_scope",
    "proxy_for_indicator", "context_for_indicator", "claim_limit_reached",
]


class Frozen(Strict):
    model_config = ConfigDict(frozen=True)


class AssessedScope(Scope):
    model_config = ConfigDict(frozen=True)


class SelectedClaim(Frozen):
    claim_id: ID
    document_id: ID
    title: Text
    institution: Institution
    source_url: URL
    attribution: Text
    rights_url: URL
    source_payload_sha256: ID
    source_block_sha256: ID
    locator: Text
    text: Text
    qualifiers: Text
    claim_type: ClaimType
    publication_date: Day
    reference_start: Day
    reference_end: Day
    indicator: Indicator
    event: Event
    geography: Geography
    relation: Relation
    use: Use
    covered_measures: tuple[Measure, ...]


class OfficialMovement(Frozen):
    """Existing endpoint calculation, with independently established vintage availability."""

    indicator: Indicator
    event: Event
    geography: Geography
    value: str = Field(pattern=r"^-?\d+\.\d{2}$")
    unit: Literal["percent", "percentage_points"]
    direction: Literal["up", "down", "flat"]
    first_observed_on: Day
    last_observed_on: Day
    available_on: Day | None
    citation: str
    source_url: URL
    attribution: Text

    @model_validator(mode="after")
    def validate_series(self):
        catalog = CATALOG[self.indicator]
        expected_unit = "percent" if self.indicator in {"eur-usd", "m3-stock"} else "percentage_points"
        if self.citation != f"series:{self.indicator}" or self.unit != expected_unit:
            raise ValueError("Movement citation or unit does not match the official indicator")
        if (self.source_url, self.attribution) != (catalog.source_url, catalog.attribution):
            raise ValueError("Movement source differs from the official catalog")
        if catalog.geography != "bilateral" and self.geography != "euro-area":
            raise ValueError("This official series measures the euro area")
        if self.first_observed_on >= self.last_observed_on:
            raise ValueError("An endpoint movement requires two ordered observations")
        if self.available_on and self.available_on < self.last_observed_on:
            raise ValueError("Series vintage cannot predate the last observation")
        value = Decimal(self.value)
        if value and self.direction != ("down" if value < 0 else "up"):
            raise ValueError("Movement direction disagrees with its signed value")
        return self


class EvidenceAssessment(Frozen):
    schema_version: Literal["m7d-v1"] = "m7d-v1"
    corpus_sha256: ID
    scope: AssessedScope
    status: Sufficiency
    limitations: tuple[Limitation, ...]
    selected_claims: tuple[SelectedClaim, ...]
    official_movement: OfficialMovement | None

    @property
    def grounding_claims(self) -> tuple[SelectedClaim, ...]:
        """Citable facts only. Context stays separate from support for the request."""
        return tuple(claim for claim in self.selected_claims if claim.use == "supports_requested_fact")
