"""Small, local lexical-vector index over reviewed institutional material."""

from __future__ import annotations

from collections import Counter
from datetime import date
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from .models import Evidence


MAX_CORPUS_BYTES = 120_000
MAX_DOCUMENTS = 16
MAX_HITS = 3
_OFFICIAL_HOSTS = {"ecb.europa.eu", "ec.europa.eu", "eur-lex.europa.eu"}


def tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    return re.findall(r"[a-z0-9]{2,}", "".join(char for char in normalized if not unicodedata.combining(char)))


class CorpusDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,47}$")
    title: str = Field(min_length=5, max_length=220)
    url: str = Field(max_length=500)
    published_on: date
    rights_url: str = Field(max_length=500)
    attribution: str = Field(min_length=3, max_length=180)
    topics: list[str] = Field(min_length=1, max_length=10)
    text: str = Field(min_length=40, max_length=5500)
    excerpt: str | None = Field(default=None, max_length=350)

    @field_validator("url", "rights_url")
    @classmethod
    def official_urls(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.netloc.lower().removeprefix("www.").removeprefix("data.")
        if parsed.scheme != "https" or host not in _OFFICIAL_HOSTS or not parsed.path:
            raise ValueError("corpus source is outside the approved institutional domains")
        return value

    @field_validator("topics")
    @classmethod
    def topic_codes(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(not re.fullmatch(r"[a-z][a-z-]{1,20}", item) for item in values):
            raise ValueError("invalid corpus topics")
        return values


class LexicalEmbeddings(Embeddings):
    """Normalized TF-IDF term vectors. No model download or external service."""

    def __init__(self, texts: list[str]):
        document_frequency = Counter(word for text in texts for word in set(tokens(text)))
        self.words = sorted(document_frequency)
        self.weights = [math.log((1 + len(texts)) / (1 + document_frequency[word])) + 1 for word in self.words]

    def embed_query(self, text: str) -> list[float]:
        counts = Counter(tokens(text))
        vector = [counts[word] * weight for word, weight in zip(self.words, self.weights, strict=True)]
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class CorpusIndex:
    def __init__(self, documents: list[CorpusDocument]):
        if not 1 <= len(documents) <= MAX_DOCUMENTS or len({doc.id for doc in documents}) != len(documents):
            raise ValueError("invalid research corpus inventory")
        self.chunks: list[Document] = []
        for item in documents:
            words = item.text.split()
            for offset in range(0, len(words), 80):
                page_content = " ".join(words[offset:offset + 96])
                self.chunks.append(Document(
                    page_content=page_content, id=f"doc:{item.id}:{offset // 80 + 1}",
                    metadata=item.model_dump(exclude={"text", "excerpt"}),
                ))
                if offset + 96 >= len(words):
                    break
        texts = [
            f"{chunk.metadata['title']} {' '.join(chunk.metadata['topics'])} {chunk.page_content}"
            for chunk in self.chunks
        ]
        self.embeddings = LexicalEmbeddings(texts)
        self.vectors = self.embeddings.embed_documents(texts)

    @classmethod
    def load(cls, path: Path) -> CorpusIndex:
        with path.open("rb") as source:
            content = source.read(MAX_CORPUS_BYTES + 1)
        if len(content) > MAX_CORPUS_BYTES:
            raise ValueError("research corpus exceeds size limit")
        return cls(TypeAdapter(list[CorpusDocument]).validate_json(content))

    def retrieve(self, question: str, topics: tuple[str, ...], cutoff: date) -> list[Document]:
        topic_set = set(topics)
        query = self.embeddings.embed_query(f"{question} {' '.join(topics)}")
        ranked = []
        for index, (chunk, vector) in enumerate(zip(self.chunks, self.vectors, strict=True)):
            meta = chunk.metadata
            if meta["published_on"] > cutoff or not topic_set.intersection(meta["topics"]):
                continue
            score = sum(left * right for left, right in zip(query, vector, strict=True))
            if score >= 0.025:
                ranked.append((score, index))
        return [self.chunks[index] for _, index in sorted(ranked, key=lambda hit: (-hit[0], hit[1]))[:MAX_HITS]]


def document_evidence(document: Document) -> Evidence:
    meta = document.metadata
    return Evidence(
        id=document.id, kind="document", title=meta["title"], url=meta["url"],
        attribution=meta["attribution"], published_on=meta["published_on"],
    )
