"""Claim-level M7.C retrieval over the reviewed M7.B snapshot."""

from datetime import date
from functools import lru_cache
import os
from pathlib import Path

from ..m7c.corpus import Corpus, Scope
from ..m7d.gate import MEASURES, PROXY_MEASURES
from ..m7d.predicates import claim_predicates
from ..m7d.ranking import LexicalRanker
from ..macro_data.api import MacroWindow
from .models import Claim, EventId, IndicatorId
from .v2_models import CuratedEvidence

ROOT = Path(__file__).resolve().parents[2]
APPROVED_SNAPSHOT = "8da278254a64e0d8837b6844b292609e87a0add7cbcaed6c18f1aededd065236"


@lru_cache(maxsize=1)
def approved_corpus() -> Corpus:
    corpus = Corpus.load(ROOT / "docs/m7b")
    if (len(corpus.documents), len(corpus.claims)) != (24, 47) or corpus.fingerprint != APPROVED_SNAPSHOT:
        raise ValueError("Unexpected M7.B snapshot")
    return corpus


@lru_cache(maxsize=1)
def lexical() -> LexicalRanker:
    return LexicalRanker({key: passage.text for key, passage in approved_corpus().passages.items()
                          if passage.claim_id})


@lru_cache(maxsize=1)
def semantic(cache: str, index: str):
    from ..m7c.index import LocalE5
    from ..m7c.retrieval import HybridRetriever

    return HybridRetriever(approved_corpus(), Path(index), LocalE5(Path(cache)))


def retrieve(
    question: str, event: EventId, indicator: IndicatorId, window: MacroWindow, cutoff: date, *,
    strategy: str | None = None,
) -> list[tuple[CuratedEvidence, Claim]]:
    # Spot observations cannot be supported by swap-market documents.
    if indicator == "eur-usd":
        return []
    genai = event == "generative-ai"
    corpus = approved_corpus()
    scope = Scope(event="genai" if genai else event, indicator=indicator, cutoff=cutoff.isoformat(),
                  geography="eu" if genai else "euro-area",
                  relations=("proxy",) if genai else ("direct", "context"))
    measures = PROXY_MEASURES | {"ai_purpose"} if genai else MEASURES[indicator]
    candidates = []
    for passage_id in corpus.eligible(scope):
        claim_id = corpus.passages[passage_id].claim_id
        if claim_id is None:
            continue
        claim = corpus.claims[claim_id]
        if (claim.type in {"observed", "institutional_interpretation"}
                and window.start.isoformat() <= claim.historical_period.start
                and claim.historical_period.end <= min(window.end, cutoff).isoformat()
                and any(predicate.measure in measures for predicate in claim_predicates(claim))):
            candidates.append(passage_id)
    if not candidates:
        return []

    if strategy is None:
        strategy = os.environ.get("MARKETPULSE_RESEARCH_V2_RETRIEVER", "tfidf")
    if strategy == "tfidf":
        ranked = lexical().rank(question, tuple(candidates))
        ranked = [row for row in ranked if row.score > 0]
    elif strategy == "e5":
        cache = os.environ["MARKETPULSE_RESEARCH_V2_E5_CACHE"]
        index = os.environ.get("MARKETPULSE_RESEARCH_V2_E5_INDEX", str(ROOT / "data/m7c/index.sqlite3"))
        ranked = semantic(cache, index).semantic_rank(question, tuple(candidates))
    else:
        raise ValueError("Unknown Research v2 retrieval strategy")

    # Preserve a contemporaneous institutional interpretation alongside
    # observed documentary context when both types are available.
    selected = []
    used_types = set()
    for row in ranked:
        claim = corpus.claims[corpus.passages[row.passage_id].claim_id]
        if claim.type in used_types:
            continue
        used_types.add(claim.type)
        doc = corpus.documents[claim.document_id]
        source = CuratedEvidence(
            id=row.passage_id, kind="document", title=doc.title, url=claim.primary_source,
            attribution=doc.rights.attribution, rights_url=doc.rights.policy_url,
            published_on=date.fromisoformat(claim.publication_date),
            period_start=date.fromisoformat(claim.historical_period.start),
            period_end=date.fromisoformat(claim.historical_period.end),
            claim_id=claim.id, document_id=doc.id, event=claim.event, indicator=indicator,
            geography=claim.geography, relation=doc.indicators[indicator], locator=claim.locator,
            qualifiers=claim.qualifiers, claim_type=claim.type,
        )
        selected.append((source, Claim(text=claim.text, citations=[source.id])))
    return selected
