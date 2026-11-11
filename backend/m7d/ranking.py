"""Interchangeable claim-only ordering. Scores never determine sufficiency."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import numpy as np

from backend.m7c.index import LocalE5, VECTOR
from backend.m7c.retrieval import Ranked, ordered, tokenize


class Ranker(Protocol):
    def rank(self, question: str, candidates: tuple[str, ...]) -> list[Ranked]: ...


class LexicalRanker:
    """Cosine TF-IDF using the M7.C accent-insensitive tokenizer."""

    def __init__(self, texts: Mapping[str, str]):
        self.positions = {key: position for position, key in enumerate(texts)}
        self.vocabulary = {
            word: position for position, word in enumerate(sorted({w for text in texts.values() for w in tokenize(text)}))
        }
        self.weights = np.ones(len(self.vocabulary))
        self.matrix = np.array([self._vector(text) for text in texts.values()])
        if texts:
            self.weights = np.log((1 + len(texts)) / (1 + np.count_nonzero(self.matrix, axis=0))) + 1
            self.matrix *= self.weights
            self.matrix /= np.maximum(np.linalg.norm(self.matrix, axis=1, keepdims=True), 1e-12)

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(len(self.vocabulary))
        for word in tokenize(text):
            if word in self.vocabulary:
                vector[self.vocabulary[word]] += 1
        return vector

    def rank(self, question: str, candidates: tuple[str, ...]) -> list[Ranked]:
        if not candidates:
            return []
        query = self._vector(question) * self.weights
        query /= max(float(np.linalg.norm(query)), 1e-12)
        scores = self.matrix[[self.positions[key] for key in candidates]] @ query
        return ordered({key: float(score) for key, score in zip(candidates, scores, strict=True)})


class SemanticRanker:
    """Multilingual E5, with fresh claim-only vectors held in memory."""

    def __init__(self, texts: Mapping[str, str], model: LocalE5):
        self.model = model
        self.positions = {key: position for position, key in enumerate(texts)}
        self.matrix = np.array([VECTOR.unpack(model.encode(text, query=False)) for text in texts.values()],
                               dtype=np.float32)

    def rank(self, question: str, candidates: tuple[str, ...]) -> list[Ranked]:
        if not candidates:
            return []
        query = np.array(VECTOR.unpack(self.model.encode(question, query=True)), dtype=np.float32)
        scores = self.matrix[[self.positions[key] for key in candidates]] @ query
        return ordered({key: float(score) for key, score in zip(candidates, scores, strict=True)})
