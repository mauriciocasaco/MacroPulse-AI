"""Approved M7.B evidence and mandatory query scope for the offline index."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import TypeAdapter, field_validator

from scripts.m7b_schema import Claim, Day, Decision, Document, Event, ID, Indicator, Relation, Review, Strict

Geography = Literal["euro-area", "eu", "us"]
GEO = {
    "Euro area and United States": "euro-area",
    "euro area": "euro-area",
    "euro area (EA19)": "euro-area",
    "European Union (EU27)": "eu",
    "United States; examples from task-level studies": "us",
}
ISSUERS = {
    "BCE": ("www.ecb.europa.eu", "/services/using-our-site/disclaimer/html/index.en.html"),
    "Eurostat": ("ec.europa.eu", "/eurostat/help/copyright-notice"),
    "Reserva Federal (Junta)": ("www.federalreserve.gov", "/disclaimer.htm"),
}
RELATIONS = ("direct", "context", "proxy")
MAX_INPUT_BYTES = 16 * 1024 * 1024
Record = TypeVar("Record", bound=Strict)


def digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_jsonl(path: Path, schema: type[Record]) -> list[Record]:
    with path.open("rb") as stream:
        content = stream.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError(f"JSONL exceeds the size limit: {path.name}")
    rows = []
    for line_number, line in enumerate(content.splitlines(), 1):
        try:
            rows.append(schema.model_validate_json(line))
        except ValueError:
            raise ValueError(f"Invalid schema in {path.name}, line {line_number}") from None
    return rows


def unique(rows, key):
    result = {key(row): row for row in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate record ID in the evidence inventory")
    return result


def read_reviews(path: Path) -> dict[str, Review]:
    with path.open("rb") as stream:
        content = stream.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError("Editorial review exceeds the size limit")
    try:
        return TypeAdapter(dict[ID, Review]).validate_json(content, strict=True)
    except ValueError:
        raise ValueError("Invalid M7.B editorial review schema") from None


class Scope(Strict):
    event: Event
    indicator: Indicator
    cutoff: Day
    geography: Geography
    relations: tuple[Relation, ...] = RELATIONS

    @field_validator("relations")
    @classmethod
    def nonempty_unique_relations(cls, values):
        if not values or len(values) != len(set(values)):
            raise ValueError("Select at least one unique evidence relation")
        return values


@dataclass(frozen=True)
class Passage:
    id: str
    document_id: str
    text: str
    content_sha256: str
    claim_id: str | None


def evidence_text(doc: Document, claim: Claim | None) -> str:
    # Explicit section labels preserve the difference between observations and hypotheses.
    if claim is None:
        return "\n".join([
            doc.title, f"Geography: {doc.geography}", f"Mechanism: {doc.mechanism}",
            *(f"{key.replace('_', ' ')}: {value}" for key, value in doc.summary.model_dump().items() if value),
        ])
    return "\n".join([
        doc.title, f"{claim.type.replace('_', ' ')}: {claim.text}", f"Qualifiers: {claim.qualifiers}",
    ])


def validate_approval(doc: Document, decision: Decision, review: Review) -> None:
    if (decision.status != "approved" or decision.rights_status != "scoped_derivatives_verified"
            or decision.source_identity != "verified" or decision.source_access != "accessible"):
        raise ValueError("Document lacks verified editorial approval")
    if (doc.id != doc.m7a_id or not doc.indicators or not set(doc.claim_ids)
            or len(set(doc.claim_ids)) != len(doc.claim_ids)
            or doc.geography not in GEO or doc.institution not in ISSUERS
            or doc.reviewed_at[:10] < doc.publication_date
            or doc.historical_period.start > doc.publication_date):
        raise ValueError("Invalid document provenance, indicators or geography")
    if (decision.original_url, decision.rights_url, decision.institution, decision.event,
        decision.m7a_publication_date, decision.source_payload_sha256, decision.normalized_text_sha256,
        decision.checked_at) != (
            doc.primary_source, doc.rights.policy_url, doc.institution, doc.event, doc.publication_date,
            doc.source_payload_sha256, doc.normalized_text_sha256, doc.reviewed_at,
    ):
        raise ValueError("Editorial approval does not match the source version")
    host, policy_path = ISSUERS[doc.institution]
    parsed = urlsplit(doc.primary_source)
    if (parsed.scheme != "https" or parsed.netloc != host or not parsed.path or parsed.fragment
            or "\\" in doc.primary_source or doc.rights.policy_url != f"https://{host}{policy_path}"):
        raise ValueError("Unapproved source or rights URL")
    if (review.rights_status != "scoped_derivatives_verified" or not review.claims
            or (doc.summary, doc.mechanism, doc.indicators, doc.geography, doc.historical_period,
                doc.rights.specific_evidence, doc.rights.policy_sha256, doc.source_payload_sha256,
                doc.normalized_text_sha256) != (
                review.summary, review.mechanism, review.indicators, review.geography, review.historical_period,
                review.rights_evidence, review.reviewed_policy_sha256, review.reviewed_payload_sha256,
                review.reviewed_text_sha256,
            )):
        raise ValueError("Document content or rights differ from the M7.B editorial review")


@dataclass
class Corpus:
    documents: dict[str, Document]
    claims: dict[str, Claim]
    passages: dict[str, Passage]
    fingerprint: str
    groups: dict[tuple[str, str, str], tuple[str, ...]]

    @classmethod
    def load(cls, directory: Path) -> Corpus:
        docs = unique(read_jsonl(directory / "curated-documents.jsonl", Document), lambda row: row.id)
        claims = unique(read_jsonl(directory / "evidence-claims.jsonl", Claim), lambda row: row.id)
        decisions = unique(read_jsonl(directory / "review-decisions.jsonl", Decision), lambda row: row.document_id)
        reviews = read_reviews(directory / "editorial-review.json")
        if decisions.keys() != reviews.keys() or any(row.status != reviews[id].status for id, row in decisions.items()):
            raise ValueError("Decision statuses differ from the M7.B editorial review")
        if not docs or set(docs) != {id for id, row in decisions.items() if row.status == "approved"}:
            raise ValueError("Corpus inventory differs from the approved document set")
        declared = {id for doc in docs.values() for id in doc.claim_ids}
        if set(claims) != declared or sum(len(doc.claim_ids) for doc in docs.values()) != len(claims):
            raise ValueError("Claim inventory does not match approved documents")
        passages = {}
        groups = {}
        seen_sources = set()
        seen_hashes = set()
        for id, doc in sorted(docs.items()):
            review = reviews[id]
            validate_approval(doc, decisions[id], review)
            approved_claims = {
                json_bytes({**draft.model_dump(mode="json", exclude={"historical_period"}),
                            "historical_period": (draft.historical_period or doc.historical_period).model_dump(mode="json")})
                for draft in review.claims
            }
            published_claims = {
                json_bytes(claims[claim_id].model_dump(mode="json", include={
                    "text", "type", "locator", "source_block_sha256", "indicators", "qualifiers", "historical_period",
                })) for claim_id in doc.claim_ids
            }
            if len(approved_claims) != len(doc.claim_ids) or approved_claims != published_claims:
                raise ValueError("Claim content differs from the M7.B editorial review")
            if doc.primary_source in seen_sources or doc.normalized_text_sha256 in seen_hashes:
                raise ValueError("Duplicate approved source or source content")
            seen_sources.add(doc.primary_source)
            seen_hashes.add(doc.normalized_text_sha256)
            for indicator in doc.indicators:
                groups.setdefault((doc.event, indicator, GEO[doc.geography]), []).append(id)
            for claim_id in (None, *doc.claim_ids):
                claim = claims[claim_id] if claim_id else None
                if claim and (claim.document_id != id or claim.primary_source != doc.primary_source
                              or claim.event != doc.event or claim.geography != doc.geography
                              or claim.publication_date != doc.publication_date
                              or not set(claim.indicators) <= doc.indicators.keys()
                              or (claim.type == "observed" and claim.historical_period.end > doc.publication_date)):
                    raise ValueError("Claim is inconsistent with its approved document")
                text = evidence_text(doc, claim)
                key = f"claim:{claim_id}" if claim_id else f"summary:{id}"
                passages[key] = Passage(key, id, text, digest(text.encode("utf-8")), claim_id)
        fingerprint = digest(json_bytes({
            "format": "m7c-v1",
            "documents": [docs[id].model_dump(mode="json") for id in sorted(docs)],
            "claims": [claims[id].model_dump(mode="json") for id in sorted(claims)],
            "approved_decisions": [decisions[id].model_dump(mode="json") for id in sorted(docs)],
        }))
        return cls(docs, claims, passages, fingerprint, {key: tuple(ids) for key, ids in groups.items()})

    def eligible(self, scope: Scope) -> tuple[str, ...]:
        """Publication day is an inclusive cutoff at day precision."""
        matches = []
        for id in self.groups.get((scope.event, scope.indicator, scope.geography), ()):
            doc = self.documents[id]
            if doc.publication_date > scope.cutoff or doc.indicators[scope.indicator] not in scope.relations:
                continue
            matches.append(f"summary:{id}")
            matches.extend(f"claim:{claim_id}" for claim_id in doc.claim_ids
                           if scope.indicator in self.claims[claim_id].indicators)
        return tuple(sorted(matches))
