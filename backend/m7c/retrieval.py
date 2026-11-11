"""Filtered BM25, E5 cosine and document-level reciprocal rank fusion."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import log1p
from pathlib import Path
import re
import unicodedata

import numpy as np

from scripts.m7b_schema import Claim, Document, Relation
from .corpus import Corpus, Passage, Scope
from .index import LocalE5, VECTOR, read_vectors

RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75
MAX_HITS = 5


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return re.findall(r"[^\W_]+", "".join(c for c in normalized if not unicodedata.combining(c)))


@dataclass(frozen=True)
class Ranked:
    passage_id: str
    score: float


@dataclass(frozen=True)
class Hit:
    document: Document
    relation: Relation
    supporting_claim: Claim | None
    lexical: Ranked | None
    semantic: Ranked | None
    lexical_rank: int | None
    semantic_rank: int | None
    rrf_score: float


class BM25:
    def __init__(self, passages: dict[str, Passage]):
        self.lengths = {}
        self.postings = defaultdict(dict)
        for id, passage in passages.items():
            terms = Counter(tokenize(passage.text))
            self.lengths[id] = sum(terms.values())
            for term, count in terms.items():
                self.postings[term][id] = count

    def rank(self, query: str, eligible: tuple[str, ...]) -> list[Ranked]:
        if not eligible:
            return []
        size = len(eligible)
        allowed = set(eligible)
        mean_len = sum(self.lengths[id] for id in eligible) / size
        scores = defaultdict(float)
        for term in sorted(set(tokenize(query))):
            postings = self.postings.get(term, {})
            matched = sorted(allowed.intersection(postings))
            df = len(matched)
            if not df:
                continue
            idf = log1p((size - df + 0.5) / (df + 0.5))
            for id in matched:
                freq = postings[id]
                length_ratio = self.lengths[id] / mean_len
                scores[id] += idf * freq * (BM25_K1 + 1) / (freq + BM25_K1 * (1 - BM25_B + BM25_B * length_ratio))
        return ordered(scores)


def ordered(scores: dict[str, float]) -> list[Ranked]:
    return [Ranked(id, score) for id, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))]


def collapse(ranks: list[Ranked], passages: dict[str, Passage]) -> dict[str, tuple[int, Ranked]]:
    """Keep one vote per document. Rank positions are assigned after collapse."""
    result = {}
    for hit in ranks:
        id = passages[hit.passage_id].document_id
        if id not in result:
            result[id] = (len(result) + 1, hit)
    return result


def reciprocal_fusion(*rankings: dict[str, tuple[int, Ranked]]) -> list[tuple[str, float]]:
    scores = defaultdict(float)
    for ranking in rankings:
        for id, (rank, _) in ranking.items():
            scores[id] += 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def best_claims(*rankings: list[Ranked], corpus: Corpus) -> dict[str, str]:
    scores = defaultdict(float)
    for ranking in rankings:
        # Rank evidence claims within each document. A summary never masks a claim.
        ranks = Counter()
        for hit in ranking:
            passage = corpus.passages[hit.passage_id]
            if passage.claim_id:
                ranks[passage.document_id] += 1
                scores[passage.claim_id] += 1 / (RRF_K + ranks[passage.document_id])
    winners = {}
    for id, _ in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0])):
        winners.setdefault(corpus.claims[id].document_id, id)
    return winners


class HybridRetriever:
    """Read-only retrieval API for M7.D. The caller supplies a structured scope."""

    def __init__(self, corpus: Corpus, path: Path, model: LocalE5):
        self.corpus = corpus
        self.model = model
        vectors = read_vectors(path, corpus, model)
        self.positions = {id: i for i, id in enumerate(vectors)}
        self.matrix = np.asarray(list(vectors.values()), dtype=np.float32)
        self.matrix.flags.writeable = False
        self.norms = np.linalg.norm(self.matrix, axis=1)
        self.lexical = BM25(corpus.passages)

    def semantic_rank(self, question: str, eligible: tuple[str, ...]) -> list[Ranked]:
        if not eligible:
            return []
        query = np.asarray(VECTOR.unpack(self.model.encode(question, query=True)), dtype=np.float32)
        positions = [self.positions[id] for id in eligible]
        scores = (self.matrix[positions] @ query) / (self.norms[positions] * np.linalg.norm(query))
        return ordered({id: float(score) for id, score in zip(eligible, scores.clip(-1, 1), strict=True)})

    def search(self, question: str, scope: Scope, *, limit: int = MAX_HITS) -> list[Hit]:
        if not question.strip() or len(question) > 2000:
            raise ValueError("Query must contain between 1 and 2000 characters")
        if not 1 <= limit <= MAX_HITS:
            raise ValueError("Return at most five sources")
        eligible = self.corpus.eligible(scope)
        lexical = self.lexical.rank(question, eligible)
        semantic = self.semantic_rank(question, eligible)
        return self._fuse(lexical, semantic, scope, limit=limit)

    def _fuse(self, lexical: list[Ranked], semantic: list[Ranked], scope: Scope, *, limit: int) -> list[Hit]:
        left, right = (collapse(r, self.corpus.passages) for r in (lexical, semantic))
        claims = best_claims(lexical, semantic, corpus=self.corpus)
        hits = []
        for id, score in reciprocal_fusion(left, right)[:limit]:
            lex_rank, lex_hit = left.get(id, (None, None))
            sem_rank, sem_hit = right.get(id, (None, None))
            doc = self.corpus.documents[id]
            hits.append(Hit(doc, doc.indicators[scope.indicator], self.corpus.claims.get(claims.get(id)),
                            lex_hit, sem_hit, lex_rank, sem_rank, score))
        return hits
