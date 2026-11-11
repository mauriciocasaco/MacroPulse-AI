"""M7.C filtering and cache integrity. Optional integration uses only pinned local E5."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import sqlite3

from pydantic import ValidationError
import pytest

from backend.m7c.corpus import Corpus, Passage, Scope, digest, json_bytes
from backend.m7c.index import LocalE5, ModelUnavailable, build_index, read_vectors
from backend.m7c.retrieval import BM25, HybridRetriever, Ranked, collapse, reciprocal_fusion

ROOT = Path(__file__).resolve().parents[1]
M7B = ROOT / "docs/m7b"


@pytest.fixture(scope="module")
def corpus():
    return Corpus.load(M7B)


def scope(**changes):
    return Scope(**{**dict(event="covid", indicator="hicp-yoy", cutoff="2020-12-31",
                           geography="euro-area"), **changes})


def test_approved_corpus_preserves_all_records_and_rights(corpus):
    assert (len(corpus.documents), len(corpus.claims), len(corpus.passages)) == (24, 47, 71)
    assert len({p.content_sha256 for p in corpus.passages.values()}) == 71
    assert all(not doc.rights.original_text_chunks_authorized for doc in corpus.documents.values())
    assert all(claim.review_status == "verified_against_source" for claim in corpus.claims.values())
    assert not any("eur-usd" in doc.indicators for doc in corpus.documents.values())


@pytest.mark.parametrize("changes", [
    {"cutoff": "2020-05-19"},
    {"geography": "us"},
    {"event": "genai"},
    {"indicator": "eur-usd"},
    {"event": "ukraine", "cutoff": "2020-12-31"},
    {"event": "genai", "indicator": "real-gdp-qoq", "geography": "eu", "cutoff": "2026-09-30",
     "relations": ("direct",)},
])
def test_filters_exclude_unavailable_or_wrong_scope(corpus, changes):
    assert not corpus.eligible(scope(**changes))


def test_historical_cutoff_uses_publication_not_reference_period(corpus):
    old = scope(indicator="real-gdp-qoq", cutoff="2020-09-07")
    boundary = scope(indicator="real-gdp-qoq", cutoff="2020-09-08")
    first = {corpus.passages[id].document_id for id in corpus.eligible(old)}
    second = {corpus.passages[id].document_id for id in corpus.eligible(boundary)}
    assert len(first) == 1 and len(second - first) == 1
    added = corpus.documents[next(iter(second - first))]
    assert added.historical_period.end == "2020-06-30"
    assert added.publication_date == "2020-09-08"
    with pytest.raises(ValidationError):
        scope(cutoff="2020-09-08T09:00:00Z")


def test_only_claims_for_requested_indicator_enter_pool(corpus):
    for id in corpus.eligible(scope()):
        p = corpus.passages[id]
        if p.claim_id:
            assert "hicp-yoy" in corpus.claims[p.claim_id].indicators


@pytest.fixture
def working_copy(tmp_path):
    for name in ("curated-documents.jsonl", "evidence-claims.jsonl", "review-decisions.jsonl", "editorial-review.json"):
        shutil.copyfile(M7B / name, tmp_path / name)
    return tmp_path


@pytest.mark.parametrize(("filename", "old", "new", "error"), [
    ("review-decisions.jsonl", b'"status": "approved"', b'"status": "needs_review"', "statuses differ"),
    ("curated-documents.jsonl", b'"observed": "', b'"observed": "Injected unreviewed wording. ', "editorial review"),
    ("evidence-claims.jsonl", b'"qualifiers": "', b'"qualifiers": "Injected qualifier. ', "editorial review"),
])
def test_fail_closed_on_editorial_or_text_drift(working_copy, filename, old, new, error):
    path = working_copy / filename
    data = path.read_bytes()
    assert old in data
    path.write_bytes(data.replace(old, new, 1))
    with pytest.raises(ValueError, match=error):
        Corpus.load(working_copy)


def test_bm25_excludes_high_scoring_passages_outside_allowed_set():
    items = {
        id: Passage(id, id, value, digest(value.encode()), None)
        for id, value in {"eligible": "energy prices during lockdown",
                          "other": "energy energy energy energy prices"}.items()
    }
    ranker = BM25(items)
    assert [x.passage_id for x in ranker.rank("energý", ("eligible",))] == ["eligible"]
    assert not ranker.rank("unmatched", ("eligible",))


def test_rrf_counts_only_one_passage_per_source_per_channel():
    passages = {
        "a1": Passage("a1", "a", "", "", None),
        "a2": Passage("a2", "a", "", "", "a2"),
        "b1": Passage("b1", "b", "", "", None),
    }
    lexical = collapse([Ranked("a1", 3.0), Ranked("a2", 2.0), Ranked("b1", 1.0)], passages)
    semantic = collapse([Ranked("b1", 0.9)], passages)
    assert lexical["b"][0] == 2
    assert [id for id, _ in reciprocal_fusion(lexical, semantic)] == ["b", "a"]


def test_missing_cache_is_actionable(tmp_path):
    with pytest.raises(ModelUnavailable, match="Missing complete local E5 cache"):
        LocalE5(tmp_path)


@pytest.fixture(scope="module")
def e5():
    cache = os.environ.get("M7C_TEST_MODEL_CACHE")
    if not cache:
        pytest.skip("M7C_TEST_MODEL_CACHE is unset")
    import torch

    torch.set_num_threads(2)
    return LocalE5(Path(cache))


def test_real_e5_reproducible_incremental_build_and_query(tmp_path, corpus, e5):
    index = tmp_path / "e5.sqlite3"
    first = build_index(index, corpus, e5)
    second = build_index(index, corpus, e5)
    independent = build_index(tmp_path / "independent.sqlite3", corpus, e5)
    assert (first.generated, second.reused, second.generated) == (71, 71, 0)
    assert first.vectors_sha256 == second.vectors_sha256 == independent.vectors_sha256

    retriever = HybridRetriever(corpus, index, e5)
    hits = retriever.search("¿Cuánto aportó la energía a la inflación de abril de 2020?", scope())
    assert hits and all(hit.semantic is not None for hit in hits)
    assert any(hit.lexical is not None for hit in hits)
    assert len(hits) == len({hit.document.id for hit in hits})
    assert all(hit.supporting_claim and "hicp-yoy" in hit.supporting_claim.indicators for hit in hits)
    assert not retriever.search("Spot exchange rate during COVID-19", scope(indicator="eur-usd"))

    # Synthetic local edit to exercise index invalidation and selective vector reuse.
    id, item = next(iter(corpus.passages.items()))
    new_text = item.text + "\nSynthetic test marker."
    changed = replace(corpus, passages={**corpus.passages, id: replace(
        item, text=new_text, content_sha256=digest(new_text.encode()),
    )}, fingerprint=digest(json_bytes([corpus.fingerprint, "synthetic test marker"])))
    with pytest.raises(ValueError, match="fingerprint changed"):
        read_vectors(index, changed, e5)
    delta = build_index(index, changed, e5)
    assert (delta.generated, delta.reused, delta.removed) == (1, 70, 1)

    # A failed rebuild rolls back deletions and metadata updates.
    long_text = "energy " * 800
    bad = replace(changed, passages={**changed.passages, id: replace(
        item, text=long_text, content_sha256=digest(long_text.encode()),
    )}, fingerprint=digest(b"synthetic invalid input"))
    with pytest.raises(ValueError, match="token budget"):
        build_index(index, bad, e5)
    assert len(read_vectors(index, changed, e5)) == 71

    with sqlite3.connect(index) as db:
        db.execute("UPDATE vectors SET embedding = zeroblob(length(embedding)) WHERE content_sha256 = ?",
                   (changed.passages[id].content_sha256,))
    with pytest.raises(ValueError, match="Corrupt vector"):
        read_vectors(index, changed, e5)
