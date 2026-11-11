"""M7.B tests use synthetic sources and local files only."""
from copy import deepcopy
from io import BytesIO

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts import m7b_curate as b
from scripts.m7b_schema import Document


SOURCE = b.m7a.ECB + "/press/pr/date/2020/html/ecb.synthetic.en.html"
POLICY = b.m7a.POLICY["BCE"]
DAY = "2020-06-01"
TIME = "2026-11-11T15:00:00+00:00"
LOC = "Synthetic release / ¶1"
ID = b.digest(SOURCE)
PAYLOAD = b.digest("raw")
TEXT = b.digest("normalized")
RIGHTS = b.digest("rights")
BLOCK = b.digest("source block")


def fake_row():
    return {"id": ID, "original_url": SOURCE, "canonical_url": SOURCE, "institution": "BCE",
            "event": "covid", "title": "Synthetic release", "publication_date": DAY,
            "kind": "decision", "publication_evidence": {}, "content_sha256": TEXT}


def fake_inputs():
    row = fake_row()
    source = {"id": ID, "url": SOURCE, "institution": "BCE", "checked_at": TIME,
              "access": "accessible", "identity": "verified", "notices": [], "title": row["title"],
              "kind": "policy_decision", "final_url": SOURCE, "media_type": "text/html", "page_date": DAY,
              "payload_sha256": PAYLOAD, "text_sha256": TEXT, "comparison_sha256": TEXT,
              "blocks": {b.digest(LOC): BLOCK}}
    snap = {"as_of": TIME, "requests": 2, "documents": {ID: source},
            "policies": {"BCE": {"url": POLICY, "verified": True, "sha256": RIGHTS, "basis": "Synthetic terms"}}}
    review = {"status": "approved", "reason": "Synthetic review",
              "content_review": "Synthetic source relation verified", "content_locator": LOC,
              "rights_status": "scoped_derivatives_verified", "rights_evidence": "Synthetic rights",
              "geography": "euro area", "historical_period": {"start": DAY, "end": DAY},
              "indicators": {"deposit-rate": "direct"}, "mechanism": "Synthetic monetary response",
              "summary": {"observed": "Synthetic example of a formal rate decision. " * 10,
                          "institutional_interpretation": None, "projection_or_expectation": None,
                          "unconfirmed_hypothesis": None,
                          "limitations": "Synthetic material for unit testing only. " * 5},
              "claims": [{"text": "Synthetic policy decision", "type": "observed", "locator": LOC,
                          "source_block_sha256": BLOCK, "indicators": ["deposit-rate"], "qualifiers": "Synthetic only"}],
              "reviewed_payload_sha256": PAYLOAD, "reviewed_text_sha256": TEXT,
              "reviewed_policy_sha256": RIGHTS}
    return {ID: row}, [ID], snap, {ID: review}


def test_html_identity_source_and_removes_navigation():
    data = b'''<html lang="en"><head><meta name="citation_online_date" content="1 June 2020"></head>
        <main><h1>Synthetic release</h1><nav><p>Navigation not an evidence paragraph and must be removed.</p></nav>
        <p>The policy rate was unchanged in the synthetic decision.</p>
        <figure><p>Caption not an evidence paragraph and must be removed.</p></figure></main></html>'''
    blocks, title, day, _, kind, _ = b.extract(data, "text/html", fake_row())
    assert (title, day, kind) == ("Synthetic release", DAY, "policy_decision")
    assert blocks == [(LOC, "The policy rate was unchanged in the synthetic decision.")]
    with pytest.raises(ValueError, match="date changed"):
        b.extract(data.replace(b"1 June", b"2 June"), "text/html", fake_row())
    with pytest.raises(ValueError, match="outside the document institution"):
        b.check_source(b.m7a.BIS + "/publications/synthetic", "BCE")


def test_pdf_page_one_requires_title_date_and_publisher():
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject(k): NameObject(v) for k, v in (
        ("/Type", "/Font"), ("/Subtype", "/Type1"), ("/BaseFont", "/Helvetica"),
    )})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 720 Td (Eurostat - 1 June 2020 - Synthetic release) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    blocks, *_ = b.pdf_blocks(buffer.getvalue(), "Synthetic release", DAY)
    assert blocks == [("p1", "Eurostat - 1 June 2020 - Synthetic release")]
    with pytest.raises(ValueError, match="title or publication date"):
        b.pdf_blocks(buffer.getvalue(), "Synthetic release", "2020-06-02")


def test_valid_build_links_claims_and_rights():
    docs, claims, decisions = b.build_records(*fake_inputs())
    assert docs[0].claim_ids == [claims[0].id]
    assert docs[0].m7a_id == claims[0].document_id == decisions[0].document_id == ID
    assert docs[0].primary_source == claims[0].primary_source == SOURCE
    assert docs[0].rights.original_text_chunks_authorized is False
    assert claims[0].review_status == "verified_against_source"
    with pytest.raises(ValidationError):
        Document.model_validate({**docs[0].model_dump(), "raw_body": "must never be persisted"})


@pytest.mark.parametrize(("changed", "message"), [
    ({"payload_sha256": b.digest("different payload")}, "version or policy"),
    ({"blocks": {b.digest(LOC): b.digest("different block")}}, "locator"),
    ({"page_date": "2020-06-02"}, "chronology"),
    ({"identity": "unverified"}, "rights evidence"),
    ({"notices": ["named_authorship"]}, "rights evidence"),
    ({"notices": ["restrictive_notice"]}, "rights evidence"),
])
def test_approval_fails_closed_on_source_or_permission_changes(changed, message):
    inv, proposal, snap, review = fake_inputs()
    snap["documents"][ID].update(changed)
    with pytest.raises(ValueError, match=message):
        b.build_records(inv, proposal, snap, review)


def test_unapproved_content_cannot_be_carried_or_emitted():
    inv, proposal, snap, review = fake_inputs()
    review[ID].update(status="needs_review", rights_status="not_cleared")
    with pytest.raises(ValueError, match="unapproved entry"):
        b.build_records(inv, proposal, snap, review)
    review[ID] = {k: review[ID][k] for k in ("status", "reason", "content_review", "rights_status", "rights_evidence")}
    docs, claims, decisions = b.build_records(inv, proposal, snap, review)
    assert not docs and not claims and decisions[0].status == "needs_review"
    snap["documents"][ID]["body"] = "untrusted raw source"
    with pytest.raises(ValidationError):
        b.build_records(inv, proposal, snap, review)


def test_duplicate_content_cannot_be_approved_twice():
    inv, proposal, snap, reviews = fake_inputs()
    other_url = SOURCE.replace("synthetic", "another")
    other = b.digest(other_url)
    inv[other] = {**inv[ID], "id": other, "original_url": other_url, "canonical_url": other_url}
    snap["documents"][other] = {**snap["documents"][ID], "id": other, "url": other_url, "final_url": other_url}
    reviews[other] = deepcopy(reviews[ID])
    with pytest.raises(ValueError, match="duplicate normalized content"):
        b.build_records(inv, proposal + [other], snap, reviews)


def test_claim_date_and_indicator_must_be_consistent():
    inv, proposal, snap, review = fake_inputs()
    claim = review[ID]["claims"][0]
    claim["indicators"] = ["m3-stock"]
    with pytest.raises(ValueError, match="subset"):
        b.build_records(inv, proposal, snap, review)
    claim["indicators"] = ["deposit-rate"]
    claim["historical_period"] = {"start": DAY, "end": "2020-06-30"}
    with pytest.raises(ValueError, match="after publication"):
        b.build_records(inv, proposal, snap, review)


def test_bundled_curated_snapshot_rebuilds_byte_for_byte(tmp_path):
    out = tmp_path / "out"
    b.build(b.M7B / "source-checks.json", b.M7B / "editorial-review.json", out)
    for basename in b.OUT_TYPES:
        name = f"{basename}.jsonl"
        assert (out / name).read_bytes() == (b.M7B / name).read_text(encoding="utf-8").encode()
    approved = {r["id"] for r in b.read_rows(out / "curated-documents.jsonl")}
    claims = b.read_rows(out / "evidence-claims.jsonl")
    decisions = b.read_rows(out / "review-decisions.jsonl")
    assert approved == {r["document_id"] for r in decisions if r["status"] == "approved"}
    assert approved == {r["document_id"] for r in claims}
    assert {r["replaces"] for r in decisions if r["replaces"]} <= {
        r["document_id"] for r in decisions if r["status"] == "rejected"
    }
