"""Embedding provider contract and exact-version storage for approved text."""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
from typing import Protocol

import psycopg


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    revision: str
    dimensions: int

    def __post_init__(self):
        if not self.model_id.strip() or not self.revision.strip() or not 1 <= self.dimensions <= 16000:
            raise ValueError("invalid embedding model specification")


class EmbeddingModel(Protocol):
    spec: ModelSpec

    def embed(self, text: str, *, query: bool) -> Sequence[float]: ...


class E5Small:
    spec = ModelSpec("intfloat/multilingual-e5-small", "614241f622f53c4eeff9890bdc4f31cfecc418b3", 384)

    def __init__(self, cache_directory: Path):
        """Load the pinned revision from a standard Hugging Face local cache."""
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(
            self.spec.model_id,
            revision=self.spec.revision,
            cache_folder=str(cache_directory),
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs={"use_safetensors": True},
        )
        if self._model.get_sentence_embedding_dimension() != self.spec.dimensions:
            raise ValueError("cached model dimensions do not match the specification")

    def embed(self, text: str, *, query: bool) -> Sequence[float]:
        prefix = "query: " if query else "passage: "
        return self._model.encode(
            prefix + text, normalize_embeddings=True, show_progress_bar=False,
        ).tolist()


def vector_literal(values: Sequence[float], spec: ModelSpec) -> str:
    if len(values) != spec.dimensions:
        raise ValueError("embedding dimensions do not match the model specification")
    if any(isinstance(value, (bool, str, bytes)) for value in values):
        raise ValueError("embedding elements must be real numbers")
    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError, OverflowError):
        raise ValueError("embedding elements must be real numbers") from None
    if any(not math.isfinite(value) or abs(value) > 3.402823466e38 for value in vector):
        raise ValueError("embedding elements must be finite float32 values")
    if max(map(abs, vector)) < 1.175494351e-38:
        raise ValueError("cosine similarity requires a nonzero embedding")
    # Scale in Python's float64 before pgvector casts to float32 and computes cosine.
    norm = math.hypot(*vector)
    return "[" + ",".join(str(value / norm) for value in vector) + "]"


def metadata_text(publisher: str, themes: Sequence[str], entities: Sequence[str]) -> str:
    """A labeled representation for lexical search over GKG metadata."""
    return "\n".join((
        "Provider metadata",
        f"Publisher: {publisher}",
        f"Themes: {', '.join(themes)}",
        f"Organizations: {', '.join(entities)}",
    ))


def embed_record(connection: psycopg.Connection, record_id: int, model: EmbeddingModel) -> str:
    """Write one approved document vector. Retry safely after provider or database failure."""
    spec = model.spec
    with connection.transaction():
        record = connection.execute("""
            SELECT content FROM news.article_records
            WHERE id = %s AND embedding_allowed AND rights_basis IS NOT NULL AND content IS NOT NULL
            FOR UPDATE
        """, (record_id,)).fetchone()
        if record is None:
            raise ValueError("record has no approved embedding input")
        text = record[0]
        fingerprint = sha256(text.encode("utf-8")).hexdigest()
        key = record_id, spec.model_id, spec.revision
        connection.execute("""
            INSERT INTO news.embedding_models (id, revision, dimensions)
            VALUES (%s, %s, %s) ON CONFLICT (id, revision) DO NOTHING
        """, (spec.model_id, spec.revision, spec.dimensions))
        registered = connection.execute("""
            SELECT dimensions FROM news.embedding_models WHERE id = %s AND revision = %s
        """, key[1:]).fetchone()
        if registered[0] != spec.dimensions:
            raise ValueError("stored model version has incompatible dimensions")
        previous = connection.execute("""
            SELECT content_sha256 FROM news.record_embeddings
            WHERE (record_id, model_id, model_revision) = (%s, %s, %s)
        """, key).fetchone()
        if previous is not None and previous[0] == fingerprint:
            return "unchanged"
        vector = vector_literal(model.embed(text, query=False), spec)
        connection.execute("""
            INSERT INTO news.record_embeddings
                (record_id, model_id, model_revision, dimensions, content_sha256, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::extensions.vector)
            ON CONFLICT (record_id, model_id, model_revision) DO UPDATE
            SET content_sha256 = EXCLUDED.content_sha256, embedding = EXCLUDED.embedding,
                embedded_at = clock_timestamp()
        """, (*key, spec.dimensions, fingerprint, vector))
    return "updated" if previous else "created"
