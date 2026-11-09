"""Retrieve attributed research evidence with SQL scope and exact cosine ranking."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from backend.market_data.catalog import ASSET_SOURCES

from .assets import normalize_terms
from .embeddings import EmbeddingModel, metadata_text, vector_literal
from .gdelt import utc


_SYMBOLS = {mapping.symbol.upper(): asset for asset, mapping in ASSET_SOURCES.items()}
_STOP_WORDS = set("a an and about by did do for from how in of on or that the to was were what when where which why with "
                  "con de del el en la las lo los o para por que qué se un una y".split())
_WINDOWS = {
    "observed": "provider_observed_at",
    "published": "reported_published_at",
    "event": "event_at",
}


@dataclass(frozen=True)
class Search:
    question: str
    assets: tuple[str, ...]
    start: datetime
    end: datetime
    sources: tuple[str, ...] = ()
    window_basis: Literal["observed", "published", "event"] = "observed"
    as_of: datetime | None = None
    as_of_mode: Literal["system", "archive"] = "system"
    limit: int = 5
    include_synthetic: bool = False
    min_cosine_similarity: float = 0.75


def search(connection: psycopg.Connection, request: Search, model: EmbeddingModel | None = None) -> dict:
    """Return evidence and coverage limits. Windows are [start, end); cutoff is exclusive."""
    question = request.question.strip()
    if not question or len(question) > 4096 or not 1 <= request.limit <= 100:
        raise ValueError("question or result limit is invalid")
    if request.window_basis not in _WINDOWS or request.as_of_mode not in ("system", "archive"):
        raise ValueError("invalid temporal policy")
    if isinstance(request.min_cosine_similarity, bool) or not 0 < request.min_cosine_similarity <= 1:
        raise ValueError("minimum cosine similarity must be in (0, 1]")
    assets = sorted({_SYMBOLS.get(asset.upper(), asset) for asset in request.assets})
    if not assets or any(asset not in ASSET_SOURCES for asset in assets):
        raise ValueError("select one or more supported assets")
    start, end = utc(request.start), utc(request.end)
    if start >= end:
        raise ValueError("invalid retrieval window")
    as_of = utc(request.as_of) if request.as_of is not None else None
    retrospective = as_of is not None and request.as_of_mode == "archive"
    terms = sorted(set(normalize_terms(question).split()) - _STOP_WORDS)
    if len(terms) > 64:
        raise ValueError("question has too many search terms")

    vector = model_id = revision = dimensions = None
    if model is not None and not retrospective:
        spec = model.spec
        vector = vector_literal(model.embed(question, query=True), spec)
        model_id, revision, dimensions = spec.model_id, spec.revision, spec.dimensions

    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(_SEARCH.format(window=sql.Identifier(_WINDOWS[request.window_basis])), (
            assets, list(request.sources), start, end, as_of, retrospective, request.include_synthetic,
            terms, vector, model_id, revision, dimensions, request.min_cosine_similarity, request.limit,
        )).fetchall()
    evidence = [_evidence(row, model_id, revision) for row in rows]
    limitations = [
        "Coverage is limited to ingested and linked provider records.",
        "Asset relationships are metadata or corpus associations and do not establish causation.",
    ]
    if request.window_basis != "observed":
        limitations.append("Records with unknown or date-only timestamps are excluded from exact time windows.")
    if as_of is not None:
        limitations.append("Records without both precise reported publication and provider observation before the cutoff are excluded.")
    if retrospective:
        limitations.append("Retrospective archive metadata uses reported provider times as an availability proxy; asset links may have been assigned later.")
    if not any(item["cosine_similarity"] is not None for item in evidence):
        limitations.append("Results use metadata and association ranking without semantic scores.")
    else:
        limitations.append("The minimum cosine similarity is a heuristic for candidate eligibility and has not been calibrated on financial news.")
    if not evidence:
        limitations.append("No eligible evidence matched this scope; provider coverage and historical availability may be incomplete.")
    return {
        "status": "evidence_found" if evidence else "insufficient_evidence",
        "window_basis": request.window_basis,
        "window_start": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "as_of_exclusive": as_of.isoformat() if as_of is not None else None,
        "as_of_mode": request.as_of_mode if as_of is not None else None,
        "min_cosine_similarity": request.min_cosine_similarity if vector is not None else None,
        "evidence": evidence,
        "limitations": limitations,
    }


def _evidence(row: dict, model_id: str | None, revision: str | None) -> dict:
    item = {
        "evidence_id": f"news-record:{row['record_id']}",
        "article_id": row["article_id"],
        "original_url": row["source_url"],
        "canonical_url": row["canonical_url"],
        "source_id": row["source_id"],
        "source_name": row["source_name"],
        "publisher": row["publisher"],
        "external_id": row["external_id"],
        "provenance_ref": row["provenance_ref"],
        "reported_publication_raw": row["reported_publication_raw"],
        "assets": row["assets"],
        "evidence_kind": row["content_kind"] if row["passage"] is not None else "provider_metadata",
        "metadata": {
            "themes": row["themes"],
            "entities": row["entities"],
            "representation": metadata_text(row["publisher"], row["themes"], row["entities"]),
        },
        "passage": row["passage"],
        "is_synthetic": row["is_synthetic"],
        "score": row["score"],
        "score_kind": "cosine_token_overlap_association" if row["cosine_similarity"] is not None else "token_overlap_association",
        "cosine_similarity": row["cosine_similarity"],
        "semantic_model": {"id": model_id, "revision": revision} if row["cosine_similarity"] is not None else None,
    }
    for key in ("event_at", "event_on", "published_at", "published_on", "provider_observed_at", "retrieved_at"):
        item[key] = row[key].isoformat() if row[key] is not None else None
    item["publication_precision"] = _precision(row["published_at"], row["published_on"])
    item["event_precision"] = _precision(row["event_at"], row["event_on"])
    return item


def _precision(instant, day) -> str:
    if instant is not None:
        return "timestamp_reported"
    return "day" if day is not None else "unknown"


_SEARCH = sql.SQL("""
    WITH params AS (
        SELECT %s::text[] AS asset_ids, %s::text[] AS sources, %s::timestamptz AS start_at,
               %s::timestamptz AS end_at, %s::timestamptz AS as_of, %s::boolean AS retrospective,
               %s::boolean AS include_synthetic, %s::text[] AS terms,
               %s::extensions.vector AS query_vector, %s::text AS model_id, %s::text AS revision,
               %s::integer AS dimensions, %s::double precision AS min_cosine
    ), candidates AS MATERIALIZED (
        SELECT r.id AS record_id, r.article_id, r.external_id, r.source_id, r.source_url,
               a.canonical_url, coalesce(r.reported_publisher, a.publisher) AS publisher, s.name AS source_name,
               r.provenance_ref, r.themes, r.entities, r.reported_publication_raw,
               r.event_at, r.event_on, r.reported_published_at AS published_at,
               r.reported_published_on AS published_on, r.provider_observed_at, r.retrieved_at,
               r.is_synthetic, r.content_kind, links.assets,
               CASE WHEN r.public_display_allowed AND NOT p.retrospective THEN r.content END AS passage,
               CASE WHEN e.dimensions = p.dimensions THEN
                    greatest(-1.0, least(1.0, 1.0 - (e.embedding OPERATOR(extensions.<=>) p.query_vector)))
               END AS raw_cosine,
               links.association_score, p.terms, p.min_cosine,
               regexp_split_to_array(lower(concat_ws(' ',
                   regexp_replace(split_part(r.source_url, '?', 1), '^https?://[^/]+', '', 'i'),
                   array_to_string(r.themes, ' '),
                   array_to_string(r.entities, ' '), links.aliases,
                   CASE WHEN r.public_display_allowed AND NOT p.retrospective THEN r.content END
               )), '[^[:alnum:]]+') AS document_terms
        FROM params p
        JOIN news.article_records r
          ON r.{window} >= p.start_at AND r.{window} < p.end_at
         AND (cardinality(p.sources) = 0 OR r.source_id = ANY(p.sources))
         AND (p.include_synthetic OR NOT r.is_synthetic)
        JOIN news.articles a ON a.id = r.article_id
        JOIN news.sources s ON s.id = r.source_id
        JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_object(
                'asset_id', ra.asset_id, 'relationship', ra.relationship, 'confidence', ra.confidence,
                'evidence', ra.evidence, 'method_version', ra.method_version, 'linked_at', ra.linked_at
            ) ORDER BY ra.asset_id) AS assets,
            max(CASE ra.relationship WHEN 'direct' THEN 1.0 WHEN 'thematic' THEN 0.65 ELSE 0.4 END) AS association_score,
            string_agg(ra.evidence, ' ') AS aliases
            FROM news.record_assets ra
            WHERE ra.record_id = r.id AND ra.asset_id = ANY(p.asset_ids)
              AND (p.as_of IS NULL OR p.retrospective OR ra.linked_at < p.as_of)
        ) links ON links.assets IS NOT NULL
        LEFT JOIN news.record_embeddings e
          ON e.record_id = r.id AND e.model_id = p.model_id AND e.model_revision = p.revision
         AND r.embedding_allowed AND r.rights_basis IS NOT NULL
         AND e.content_sha256 = encode(sha256(convert_to(r.content, 'UTF8')), 'hex')
         AND (p.as_of IS NULL OR e.embedded_at < p.as_of)
        WHERE p.as_of IS NULL OR (
            r.reported_published_at < p.as_of AND r.provider_observed_at < p.as_of
            AND (r.evidence_updated_at IS NULL OR r.evidence_updated_at < p.as_of)
            AND (p.retrospective OR r.retrieved_at < p.as_of)
        )
    ), eligible AS (
        SELECT *, CASE WHEN raw_cosine >= min_cosine THEN raw_cosine END AS cosine_similarity
        FROM candidates
    ), scored AS (
        SELECT *, CASE WHEN cosine_similarity IS NULL THEN
            0.75 * lexical.overlap + 0.25 * association_score
        ELSE
            0.65 * ((cosine_similarity + 1.0) / 2.0) + 0.25 * lexical.overlap + 0.10 * association_score
        END::double precision AS score
        FROM eligible
        CROSS JOIN LATERAL (
            SELECT count(*)::double precision / greatest(cardinality(terms), 1) AS overlap
            FROM unnest(terms) AS token
            WHERE token = ANY(document_terms)
        ) lexical
        WHERE lexical.overlap > 0 OR cosine_similarity IS NOT NULL
    ), deduplicated AS (
        SELECT DISTINCT ON (article_id) * FROM scored
        ORDER BY article_id, score DESC, provider_observed_at DESC NULLS LAST, record_id
    )
    SELECT * FROM deduplicated
    ORDER BY score DESC, provider_observed_at DESC NULLS LAST, record_id
    LIMIT %s
""")
