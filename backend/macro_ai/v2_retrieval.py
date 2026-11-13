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
APPROVED_SNAPSHOT = "ff38a3e14748fbcc3453a46f06de3968429fa9329a619fef3b99d01be6051622"


@lru_cache(maxsize=1)
def approved_corpus() -> Corpus:
    corpus = Corpus.load(ROOT / "docs/m7b")
    if (len(corpus.documents), len(corpus.claims)) != (30, 53) or corpus.fingerprint != APPROVED_SNAPSHOT:
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
    genai = event == "generative-ai"
    if genai and indicator != "real-gdp-qoq":
        return []
    corpus = approved_corpus()
    scope = Scope(event="genai" if genai else event, indicator=indicator, cutoff=cutoff.isoformat(),
                  geography="eu" if genai else "euro-area",
                  relations=("proxy",) if genai else ("direct", "context"))
    measures = PROXY_MEASURES if genai else MEASURES[indicator]
    if indicator == "eur-usd":
        measures = {"spot_move", "spot_level"}
    candidates = []
    for passage_id in corpus.eligible(scope):
        claim_id = corpus.passages[passage_id].claim_id
        if claim_id is None:
            continue
        claim = corpus.claims[claim_id]
        if indicator == "eur-usd" and (
            claim.geography != "Euro area and United States" or "eur/usd spot" not in claim.text.casefold()
        ):
            continue
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

    ranked_claims = [(row.passage_id, corpus.claims[corpus.passages[row.passage_id].claim_id]) for row in ranked]
    if indicator == "m3-stock":
        # Prefer the published stock to growth-rate context for the stock series.
        ranked_claims.sort(key=lambda item: not any(
            predicate.measure == "m3_stock" for predicate in claim_predicates(item[1])
        ))

    # For the Ukraine rate and GDP scenarios, use a second observation
    # if there is no institutional interpretation and the periods differ.
    allow_second_observation = event == "ukraine" and indicator in {"deposit-rate", "real-gdp-qoq"}
    selected = []
    used_types = set()
    if allow_second_observation and ranked_claims:
        first_type = ranked_claims[0][1].type
        ranked_claims[1:] = sorted(ranked_claims[1:], key=lambda item: item[1].type == first_type)
    for passage_id, claim in ranked_claims:
        if claim.type in used_types:
            previous = selected[0][0]
            if (
                not allow_second_observation or claim.type != "observed" or claim.document_id == previous.document_id
                or (claim.historical_period.start, claim.historical_period.end)
                == (previous.period_start.isoformat(), previous.period_end.isoformat())
            ):
                continue
        used_types.add(claim.type)
        doc = corpus.documents[claim.document_id]
        source = CuratedEvidence(
            id=passage_id, kind="document", title=doc.title, url=claim.primary_source,
            attribution=doc.rights.attribution, rights_url=doc.rights.policy_url,
            published_on=date.fromisoformat(claim.publication_date),
            period_start=date.fromisoformat(claim.historical_period.start),
            period_end=date.fromisoformat(claim.historical_period.end),
            claim_id=claim.id, document_id=doc.id, event=claim.event, indicator=indicator,
            geography=claim.geography, relation=doc.indicators[indicator], locator=claim.locator,
            qualifiers=claim.qualifiers, claim_type=claim.type,
        )
        selected.append((source, Claim(text=claim.text, citations=[source.id])))
        if len(selected) == 2:
            break
    return selected
