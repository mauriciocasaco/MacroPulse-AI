from datetime import datetime, timezone
from hashlib import sha256

import pytest

from backend.news.editorial_index import build_index, digest, eligible, search_index
from backend.news.embeddings import ModelSpec

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
EARLIER = "2026-11-13T19:00:00+00:00"


class SyntheticModel:
    spec = ModelSpec("synthetic", "v1", 2)

    def embed(self, text, *, query):
        return [3, 4]


def fixture():
    source = {"evidence_url": "https://example.org/source", "published_on": "2026-11-13",
              "observed_at": EARLIER}
    source["source_fingerprint"] = digest(source)
    binding = {"case_version": "a" * 64, "source_fingerprint": source["source_fingerprint"]}
    scope = binding | {"decision": "allowed", "scope": "original_summary",
                       "basis_url": "https://example.org/rights", "terms_url": "https://example.org/terms",
                       "basis": "Synthetic basis", "conditions": "Synthetic conditions", "rights_actor": "reviewer-1",
                       "decided_at": EARLIER, "effective_at": EARLIER, "expires_at": None, "revoked_at": None}
    factual = scope | {"use": "factual_public"}
    factual["rights_version"] = digest(factual)
    embedding = scope | {"use": "embedding"}
    embedding["rights_version"] = digest(embedding)
    review = binding | {"decision": "approved", "reviewer_id": "reviewer-2",
                        "review_evidence_url": "https://example.org/review", "reason": "Synthetic review",
                        "factual_rights_version": factual["rights_version"], "decided_at": EARLIER}
    summary = "Un hecho económico sintético."
    fingerprint = sha256(summary.encode()).hexdigest()
    claim = binding | {"claim_id": "synthetic-1", "summary": summary, "summary_sha256": fingerprint,
                       "event": "covid", "indicator": "m3-stock"}
    item = binding | {"claim_id": claim["claim_id"], "text": summary, "rights": embedding, "review": review,
                      "content_sha256": fingerprint, "rights_version": embedding["rights_version"]}
    return {"schema_version": 1, "mode": "dry_run", "as_of": NOW.isoformat(),
            "approved_staging": [{"source": source, "claim": claim, "review": review, "rights": factual}],
            "approved_embedding_staging": [item]}


def test_authorized_exact_input_search_and_scope():
    batch = fixture()
    rows = eligible(batch, NOW)
    model = SyntheticModel()
    index = build_index(rows, model)
    assert len(index["entries"]) == 1
    assert "text" not in index["entries"][0]
    result = search_index(index, rows, model, "saldo", "covid", "m3-stock", 1)
    assert result[0]["score"] == pytest.approx(1)
    assert result[0]["url"] == "https://example.org/source"
    assert search_index(index, rows, model, "saldo", "genai", "m3-stock", 1) == []
    assert search_index(index, [], model, "saldo", "covid", "m3-stock", 1) == []
    cutoff = datetime(2026, 11, 13, 18, tzinfo=timezone.utc)
    assert search_index(index, rows, model, "saldo", "covid", "m3-stock", 1, cutoff=cutoff) == []


@pytest.mark.parametrize("change", [
    {"revoked_at": EARLIER}, {"expires_at": "2026-11-13T20:00:00+00:00"},
])
def test_expiration_or_revocation_excludes_cached_vector(change):
    batch = fixture()
    item = batch["approved_embedding_staging"][0]
    item["rights"].update(change)
    item["rights_version"] = item["rights"]["rights_version"] = digest(
        {k: v for k, v in item["rights"].items() if k != "rights_version"})
    assert eligible(batch, NOW) == []


@pytest.mark.parametrize("change", [
    {"text": "Modified input"}, {"case_version": "other"}, {"rights_version": "other"},
])
def test_version_or_content_tampering_fails_closed(change):
    batch = fixture()
    batch["approved_embedding_staging"][0].update(change)
    with pytest.raises(ValueError):
        eligible(batch, NOW)


def test_drafts_and_factual_only_never_embed():
    batch = fixture()
    batch["claim_drafts"] = batch["approved_staging"]
    batch["approved_embedding_staging"] = []
    assert eligible(batch, NOW) == []
