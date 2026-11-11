"""Offline benchmark with fixed document and claim judgments."""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
import platform
from statistics import mean, median
from time import perf_counter

import numpy as np
from pydantic import Field

from backend.macro_ai.retrieval import CorpusIndex, LexicalEmbeddings
from scripts.m7b_schema import ID
from .corpus import GEO, Scope, digest, read_jsonl
from .retrieval import (
    BM25_B, BM25_K1, MAX_HITS, RRF_K, HybridRetriever, best_claims, collapse, ordered,
)

TOPICS = {
    "eur-usd": "fx", "m3-stock": "money", "hicp-yoy": "inflation",
    "real-gdp-qoq": "gdp", "deposit-rate": "rates",
}
ONSET = {"covid": date(2020, 2, 1), "ukraine": date(2022, 2, 24), "genai": date(2023, 9, 1)}
METHODS = ("hybrid", "bm25", "e5", "tfidf_same_corpus", "legacy_runtime")


class Case(Scope):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    question: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1)
    relevant_documents: list[ID]
    relevant_claims: list[ID]
    legacy_relevant_documents: list[str]


def measure(call, repeats):
    elapsed = []
    result = None
    for _ in range(repeats):
        start = perf_counter()
        result = call()
        elapsed.append((perf_counter() - start) * 1000)
    return result, elapsed


def audit_passages(retriever, ranks):
    docs = collapse(ranks, retriever.corpus.passages)
    claims = best_claims(ranks, corpus=retriever.corpus)
    return [{"document_id": id, "claim_id": claims.get(id), "best_passage_id": item.passage_id, "score": item.score}
            for id, (_, item) in list(docs.items())[:MAX_HITS]]


def audit_hybrid(hits):
    return [{
        "document_id": hit.document.id,
        "claim_id": hit.supporting_claim.id if hit.supporting_claim else None,
        "relation": hit.relation,
        "score": hit.rrf_score,
        "bm25_rank": hit.lexical_rank,
        "e5_rank": hit.semantic_rank,
        "bm25_passage": hit.lexical.passage_id if hit.lexical else None,
        "bm25_score": hit.lexical.score if hit.lexical else None,
        "e5_passage": hit.semantic.passage_id if hit.semantic else None,
        "e5_cosine": hit.semantic.score if hit.semantic else None,
    } for hit in hits]


def hit_rate(rankings, k):
    return mean(any(id in relevant for id in ranking[:k]) for ranking, relevant in rankings) if rankings else None


def reciprocal_rank(ranking, relevant):
    return next((1 / rank for rank, id in enumerate(ranking, 1) if id in relevant), 0.0)


def metrics(rows, method, document_metadata):
    evaluated = []
    claim_evaluated = []
    true_returned = returned = duplicates = eligible_returned = 0
    negative = false_positive = temporal_ok = 0
    scope_negatives = scope_false_positive = intent_negatives = intent_false_positive = 0
    all_latencies = []
    per_indicator = {}
    legacy = method == "legacy_runtime"
    for case, runs in rows:
        run = runs[method]
        hits = run["hits"]
        ids = [hit["document_id"] for hit in hits]
        all_latencies.extend(run["latency_samples_ms"])
        rel = set(case.legacy_relevant_documents if legacy else case.relevant_documents)
        bucket = per_indicator.setdefault(case.indicator, {"returned": 0, "judged_relevant": 0, "positives": []})
        bucket["returned"] += len(ids)
        bucket["judged_relevant"] += sum(id in rel for id in ids)
        if rel:
            evaluated.append((ids, rel))
            bucket["positives"].append((ids, rel))
        if not legacy and case.relevant_claims:
            claim_evaluated.append(([hit["claim_id"] for hit in hits], set(case.relevant_claims)))
        if not case.relevant_documents:
            negative += 1
            false_positive += bool(ids)
            if run["m7b_eligible_documents"]:
                intent_negatives += 1
                intent_false_positive += bool(ids)
            else:
                scope_negatives += 1
                scope_false_positive += bool(ids)
        for id in ids:
            doc = document_metadata[id]
            if legacy:
                temporal_ok += doc["published_on"].isoformat() <= case.cutoff
                eligible_returned += TOPICS[case.indicator] in doc["topics"]
            else:
                temporal_ok += doc.publication_date <= case.cutoff
                eligible_returned += case.indicator in doc.indicators
        duplicates += len(ids) - len(set(ids))
        returned += len(ids)
        true_returned += sum(id in rel for id in ids)
    return {
        "positive_cases_in_this_corpus": len(evaluated),
        "hit_at_1": hit_rate(evaluated, 1),
        "hit_at_3": hit_rate(evaluated, 3),
        "hit_at_5": None if legacy else hit_rate(evaluated, 5),
        "mrr": mean(reciprocal_rank(ranking, rel) for ranking, rel in evaluated) if evaluated else None,
        "hit_at_3_on_all_m7b_positive_questions": sum(
            bool(set(hit["document_id"] for hit in run[method]["hits"][:3]) &
                 set(case.legacy_relevant_documents if legacy else case.relevant_documents))
            for case, run in rows if case.relevant_documents
        ) / sum(bool(case.relevant_documents) for case, _ in rows),
        "verified_claim_hit_at_3": hit_rate(claim_evaluated, 3),
        "verified_claim_hit_at_5": hit_rate(claim_evaluated, 5),
        "verified_claim_mrr": mean(reciprocal_rank(ranking, rel) for ranking, rel in claim_evaluated)
        if claim_evaluated else None,
        "judged_document_precision": true_returned / returned if returned else None,
        "publication_filter_precision": temporal_ok / returned if returned else None,
        "indicator_filter_precision": eligible_returned / returned if returned else None,
        "returned_sources": returned,
        "duplicate_document_hits": duplicates,
        "no_relevant_cases": negative,
        "no_relevant_cases_with_hits": false_positive,
        "empty_m7b_scope_cases": scope_negatives,
        "empty_m7b_scope_cases_with_hits": scope_false_positive,
        "unanswered_intent_within_m7b_scope_cases": intent_negatives,
        "unanswered_intent_within_m7b_scope_cases_with_hits": intent_false_positive,
        "latency_ms": {"median": median(all_latencies), "p95": float(np.percentile(all_latencies, 95))},
        "by_indicator": {
            indicator: {"returned": bucket["returned"], "judged_relevant": bucket["judged_relevant"],
                        "precision": bucket["judged_relevant"] / bucket["returned"] if bucket["returned"] else None,
                        "hit_at_3": hit_rate(bucket["positives"], 3)}
            for indicator, bucket in per_indicator.items()
        },
    }


def evaluate(retriever: HybridRetriever, dataset: Path, legacy_path: Path, *, repeats: int):
    corpus = retriever.corpus
    cases = read_jsonl(dataset, Case)
    if not cases or len({case.id for case in cases}) != len(cases) or not any(c.relevant_documents for c in cases):
        raise ValueError("Invalid evaluation inventory")
    legacy = CorpusIndex.load(legacy_path)
    legacy_metadata = {c.id.rsplit(":", 1)[0][4:]: c.metadata for c in legacy.chunks}
    tfidf = LexicalEmbeddings([item.text for item in corpus.passages.values()])
    tfidf_vectors = dict(zip(corpus.passages, tfidf.embed_documents([p.text for p in corpus.passages.values()]), strict=True))

    def tfidf_rank(question, eligible):
        if not eligible:
            return []
        query = tfidf.embed_query(question)
        scores = {id: sum(a * b for a, b in zip(query, tfidf_vectors[id], strict=True)) for id in eligible}
        return ordered({id: score for id, score in scores.items() if score >= 0.025})

    # Warm E5 before measuring per-query latency. No corpus, cache or model startup is timed.
    retriever.model.encode("inflation during a lockdown", query=True)
    rows = []
    for case in cases:
        scope = Scope.model_validate(case.model_dump(include=set(Scope.model_fields)))
        eligible = corpus.eligible(scope)
        pool_docs = {corpus.passages[id].document_id for id in eligible}
        pool_claims = {corpus.passages[id].claim_id for id in eligible}
        if (not set(case.relevant_documents) <= pool_docs or not set(case.relevant_claims) <= pool_claims
                or not set(case.legacy_relevant_documents) <= legacy_metadata.keys()
                or any(corpus.claims[id].document_id not in case.relevant_documents for id in case.relevant_claims)):
            raise ValueError(f"Evaluation judgment falls outside eligibility: {case.id}")
        legacy_topics = (TOPICS[case.indicator], case.event)
        hybrid, t_hybrid = measure(lambda: retriever.search(case.question, scope), repeats)
        lexical, t_lexical = measure(lambda: audit_passages(
            retriever, retriever.lexical.rank(case.question, corpus.eligible(scope))
        ), repeats)
        semantic, t_semantic = measure(lambda: audit_passages(
            retriever, retriever.semantic_rank(case.question, corpus.eligible(scope))
        ), repeats)
        tfidf_rows, t_tfidf = measure(lambda: audit_passages(
            retriever, tfidf_rank(case.question, corpus.eligible(scope))
        ), repeats)
        legacy_hits, t_legacy = measure(lambda: legacy.retrieve(
            case.question, legacy_topics, date.fromisoformat(case.cutoff), event=case.event, onset=ONSET[case.event]
        ), repeats)
        # Native legacy retrieval caps at three chunks. Collapse any repeated document for source-level comparison.
        legacy_rows = {}
        for chunk in legacy_hits:
            id = chunk.id.rsplit(":", 1)[0][4:]
            legacy_rows.setdefault(id, {"document_id": id, "claim_id": None,
                                         "best_passage_id": chunk.id, "score": chunk.metadata["similarity_score"]})
        hits_and_times = (
            (audit_hybrid(hybrid), t_hybrid),
            (lexical, t_lexical),
            (semantic, t_semantic),
            (tfidf_rows, t_tfidf),
            (list(legacy_rows.values()), t_legacy),
        )
        runs = {}
        for method, (hits, timings) in zip(METHODS, hits_and_times, strict=True):
            if method != "legacy_runtime":
                for hit in hits:
                    id = hit["document_id"]
                    doc = corpus.documents[id]
                    if (id not in pool_docs or GEO[doc.geography] != case.geography or doc.event != case.event
                            or doc.publication_date > case.cutoff or doc.indicators.get(case.indicator) not in case.relations
                            or (hit["claim_id"] is not None and hit["claim_id"] not in pool_claims)):
                        raise ValueError(f"Editorial or temporal leakage in {case.id}")
            runs[method] = {"hits": hits, "latency_samples_ms": timings, "m7b_eligible_documents": len(pool_docs)}
        rows.append((case, runs))
    return {
        "schema": "m7c-eval-v1",
        "dataset_sha256": digest(dataset.read_text(encoding="utf-8").encode("utf-8")),
        "corpus_sha256": corpus.fingerprint,
        "legacy_corpus_sha256": digest(legacy_path.read_text(encoding="utf-8").encode("utf-8")),
        "model": retriever.model.provenance,
        "model_sha256": retriever.model.fingerprint,
        "environment": {"python": platform.python_version(), "system": platform.system(),
                        "machine": platform.machine()},
        "parameters": {"bm25_k1": BM25_K1, "bm25_b": BM25_B, "rrf_k": RRF_K, "limit": MAX_HITS, "repeats": repeats},
        "metrics": {method: metrics(rows, method, legacy_metadata if method == "legacy_runtime" else corpus.documents)
                    for method in METHODS},
        "cases": [{"id": case.id, "runs": run} for case, run in rows],
    }
