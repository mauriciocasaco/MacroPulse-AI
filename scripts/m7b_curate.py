"""Bounded, read-only M7.B source inspection and deterministic corpus build."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from pypdf import PdfReader
import requests

from scripts import m7a_discovery as m7a
from scripts.m7b_schema import Claim, Decision, Document, Review, Snapshot

ROOT = Path(__file__).resolve().parents[1]
M7A = ROOT / "docs/m7a"
M7B = ROOT / "docs/m7b"
INDICATORS = {
    "EUR/USD": "eur-usd", "M3": "m3-stock", "HICP": "hicp-yoy",
    "PIB real trimestral": "real-gdp-qoq", "Tasa de depósito BCE": "deposit-rate",
}
HOSTS = {
    "BCE": "www.ecb.europa.eu", "Eurostat": "ec.europa.eu", "BIS": "www.bis.org",
    "Reserva Federal (Junta)": "www.federalreserve.gov",
}
POLICIES = {
    "BCE": ("ECB institutional information", r"may make free use", r"explicit prior written authorisation"),
    "Eurostat": ("CC BY 4.0 / EU statistical reuse", r"Creative Commons Attribution 4\.0", r"individual copyright notices"),
    "Reserva Federal (Junta)": ("US Board public domain", r"public domain", r"unless otherwise indicated"),
    "BIS": ("BIS limited extracts", r"non-commercial purposes", r"limited extract"),
}
RESTRICT = re.compile(
    r"all rights reserved|permission (?:is|required|must)|prior written (?:permission|authori[sz]ation)|"
    r"reproduction.{0,50}(?:prohibited|non.commercial)|copyright.{0,30}third.part", re.I,
)
REMOVE = "nav,header,footer,aside,script,style,figure,table,form,iframe,button,svg,.breadcrumb,.footnotes,.footnote,.source,.chart,.share"
OUT_TYPES = ("curated-documents", "evidence-claims", "review-decisions")


def digest(data):
    return sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).replace("\xad", "").replace("\u200b", "")
                    .replace("\u2212", "-").split())


def comparable(value):
    return " ".join(re.findall(r"\w+", normalize(value).casefold()))


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8", newline="\n")


def inventory():
    by_id = {r["id"]: r for r in read_rows(M7A / "candidates.jsonl")}
    proposal = load_json(M7A / "manifest.json")["proposal_ids"]
    if len(proposal) != len(set(proposal)) or not set(proposal) <= by_id.keys():
        raise ValueError("invalid proposal IDs")
    for row in read_rows(M7B / "expansion-candidates.jsonl"):
        id = row["id"]
        if id in proposal or by_id.get(id, {}).get("m85_expansion"):
            raise ValueError("duplicate expansion candidate")
        if set(row) == {"id"}:
            row = by_id[id]
        elif id in by_id:
            raise ValueError("expansion metadata cannot override M7.A")
        by_id[id] = {**row, "m85_expansion": True}
    return by_id, proposal


def check_source(url, institution):
    url = m7a.checked_url(url)
    if urlsplit(url).netloc != HOSTS[institution]:
        raise ValueError("URL outside the document institution")
    return url


def pdf_blocks(data, title, day):
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 60:
        raise ValueError("encrypted PDF or page limit")
    blocks = []
    for n, page in enumerate(reader.pages, 1):
        content = page.get_contents()
        if content and len(content.get_data()) > 4 * 1024 * 1024:
            raise ValueError("PDF page stream limit")
        text = normalize(page.extract_text() or "")
        if len(text) > 100_000:
            raise ValueError("PDF page text limit")
        if text:
            blocks.append((f"p{n}", text))
    if not blocks or blocks[0][0] != "p1":
        raise ValueError("no first-page PDF text")
    first = blocks[0][1]
    date_pattern = rf"\b0?{date.fromisoformat(day).day} {date.fromisoformat(day):%B %Y}\b"
    if comparable(title) not in comparable(first[:2400]) or not re.search(date_pattern, first[:600]):
        raise ValueError("PDF title or publication date not corroborated on page 1")
    if "eurostat" not in first[:2500].casefold():
        raise ValueError("PDF publisher not corroborated")
    return blocks, title, day, [], "statistical_release", normalize(" ".join(t for _, t in blocks))


def html_blocks(data, row):
    page = m7a.markup(data)
    source = row["institution"]
    fed = source == "Reserva Federal (Junta)"
    if not page.html or not page.html.get("lang", "").lower().startswith("en"):
        raise ValueError("unexpected HTML language")
    body = page.select_one("#article" if fed else "main")
    if not body and source == "Eurostat":
        body = page.select_one("#content") or page.select_one(".portlet-journal-content")
    if not body and source == "BIS":
        body = page.select_one("article")
    if not body:
        raise ValueError("missing source content container")
    heading = body.select_one("h3" if fed else "h1")
    if not heading:
        raise ValueError("missing primary heading")
    title = normalize(m7a.clean(heading))
    if comparable(title) != comparable(row["title"]):
        raise ValueError("HTML title changed from M7.A")
    meta = page.select_one('meta[name="citation_online_date"],meta[name="citation_publication_date"]')
    raw_date = meta.get("content") if meta else None
    if fed and (dt := body.select_one(".article__time")):
        raw_date = m7a.clean(dt)
    if not raw_date:
        date_context = m7a.clean(heading.parent.parent) if source == "Eurostat" else m7a.clean(body)[:1600]
        match = re.search(r"\b\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4}\b", date_context)
        raw_date = match[0] if match else None
    if not raw_date:
        raise ValueError("publisher date absent")
    day = m7a.dateline(raw_date)
    if day not in {row["publication_date"], row["publication_evidence"].get("page_date")}:
        raise ValueError("publication date changed from M7.A")
    notices = []
    doc_text = m7a.clean(body)
    if RESTRICT.search(doc_text):
        notices.append("restrictive_notice")
    if page.select_one('meta[name="citation_author"],.ecb-authors') or re.search(r"\bprepared by\b|\bbyline\b", doc_text, re.I):
        notices.append("named_authorship")
    if re.search(r"©|\bcopyright\b", doc_text, re.I):
        notices.append("copyright_or_credit")
    for element in body.select(REMOVE):
        if element.parent:
            element.decompose()
    blocks = []
    section = title
    for node in body.select("h2,h3,h4,p,li"):
        if node.name.startswith("h"):
            section = normalize(m7a.clean(node))
        elif not node.find_parent(["li", "p"]):
            value = normalize(m7a.clean(node))
            if len(value) >= 35:
                blocks.append((f"{section} / ¶{len(blocks) + 1}", value))
    if not blocks:
        raise ValueError("no substantive prose")
    kind = {"m3": "statistical_release", "decision": "policy_decision", "bulletin": "bulletin",
            "focus": "signed_analysis", "account": "policy_account"}.get(row["kind"], "analysis")
    if fed or "/speeches/" in row["original_url"] or "/press/key/" in row["original_url"]:
        kind = "speech"
    elif source == "Eurostat":
        kind = "statistical_release"
    return blocks, title, day, notices, kind, doc_text


def money_annex(data, row, http):
    # The dated parent release establishes the annex's identity and reuse notice.
    parent = row["parent_url"]
    parent_data, final, _, _ = http.get(check_source(parent, "BCE"))
    if parent != final:
        raise ValueError("unexpected annex parent destination")
    page = m7a.markup(parent_data)
    if not page.find("a", href=urlsplit(row["original_url"]).path):
        raise ValueError("annex not linked from parent release")
    _, title, day, notices, _, parent_text = html_blocks(
        parent_data, {**row, "kind": "m3", "original_url": parent},
    )
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) != 4:
        raise ValueError("unexpected ECB monetary annex layout")
    page = reader.pages[0]
    if len(page.get_contents().get_data()) > 4 * 1024 * 1024:
        raise ValueError("PDF page stream limit")
    content = normalize(page.extract_text() or "")
    if len(content) > 100_000 or not content.startswith(f"1 {title}"):
        raise ValueError("annex title or text size does not match")
    if RESTRICT.search(content):
        notices.append("restrictive_notice")
    return [("p1", content), ("Parent release", parent_text)], title, day, notices, "monetary_annex", parent_text


def extract(data, mime, row, *, http=None):
    if row.get("kind") == "m3_annex":
        return money_annex(data, row, http)

    return (pdf_blocks(data, row["title"], row["publication_date"])
            if mime == "application/pdf" else html_blocks(data, row))


def comparison_hash(data, mime, row):
    """Reapply M7.A's fingerprint, independently of M7.B extraction."""
    if mime == "application/pdf":
        return digest(data)
    page = m7a.markup(data)
    fed = row["institution"] == "Reserva Federal (Junta)"
    main = page.select_one("#article" if fed else "main")
    if main is None and row["institution"] not in {"BCE", "Reserva Federal (Junta)"}:
        main = page
    if main is None:
        return None
    paragraphs = [m7a.clean(p) for p in main.select("p") if len(m7a.clean(p)) >= 35]
    return digest("\n".join(paragraphs)) if paragraphs else None


def safe_error(exc):
    if isinstance(exc, ValueError) and type(exc).__module__ == "builtins":
        msg = str(exc)
        if re.fullmatch(r"[\w (),.:-]{1,120}", msg, flags=re.UNICODE) and not re.search(r"https?:|@", msg):
            return msg
    return type(exc).__name__


def inspect_one(http, row):
    result = {"id": row["id"], "url": row["original_url"], "institution": row["institution"],
              "access": "unverified", "identity": "unverified"}
    try:
        raw = check_source(row["original_url"], row["institution"])
        data, url, mime, redirects = http.get(raw, "pdf" if row["kind"] in {"stat_pdf", "m3_annex"} else "html")
        result.update(access="accessible", final_url=check_source(url, row["institution"]),
                      media_type=mime, redirects=redirects, payload_sha256=digest(data))
        if m7a.identity(url) != row["canonical_url"]:
            raise ValueError("canonical destination changed from M7.A")
        blocks, title, day, notices, kind, doc_text = extract(data, mime, row, http=http)
        old_style = comparison_hash(data, mime, row)
        result.update(identity="verified", title=title, page_date=day, kind=kind,
                      notices=notices, text_sha256=digest("\n".join(t for _, t in blocks)),
                      comparison_sha256=old_style,
                      document_reproduction_notice=bool(re.search(r"reproduction is permitted provided that the source is acknowledged", doc_text, re.I)),
                      blocks={digest(k): digest(t) for k, t in blocks})
        if mime == "application/pdf" and RESTRICT.search(doc_text):
            result["notices"].append("restrictive_notice")
        return result, blocks
    except (ValueError, requests.RequestException, OSError) as exc:
        result["error"] = safe_error(exc)
    except Exception as exc:
        # Parser errors are untrusted; no body, stack trace or publisher error is exported.
        result["error"] = type(exc).__name__
    return result, []


def inspect(out, replacement_ids=(), *, inspect_ids=()):
    by_id, proposal = inventory()
    reviewed_ids = load_json(M7B / "editorial-review.json")
    selected = list(inspect_ids) or list(dict.fromkeys([*reviewed_ids, *replacement_ids]))
    if len(selected) != len(set(selected)) or not set(selected) <= by_id.keys():
        raise ValueError("invalid or repeated M7.A ID")
    http = m7a.HTTP()
    policies = {}
    for source, url in m7a.POLICY.items():
        try:
            data, final, _, _ = http.get(check_source(url, source))
            policy = normalize(m7a.clean(m7a.markup(data)))
            label, *patterns = POLICIES[source]
            policies[source] = {"url": final, "sha256": digest(policy), "basis": label,
                                "verified": all(re.search(p, policy, re.I) for p in patterns)}
        except (ValueError, requests.RequestException) as exc:
            policies[source] = {"url": url, "verified": False, "error": safe_error(exc)}
    out.parent.mkdir(parents=True, exist_ok=True)
    records = {}
    for id in selected:
        row = by_id[id]
        result, _ = inspect_one(http, row)
        result["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        records[id] = result
        print(f"{id[:10]} {result['identity']} {result.get('error', '')}", flush=True)
    write_json(out, {"as_of": datetime.now(UTC).isoformat(timespec="seconds"), "requests": http.requests,
                     "policies": policies, "documents": records})


def approval_eligible(row, check, policy):
    if not policy.get("verified") or policy.get("url") != m7a.POLICY[row["institution"]]:
        return False
    if check.get("identity") != "verified" or check.get("access") != "accessible":
        return False
    if "restrictive_notice" in check["notices"]:
        return False
    match row["institution"]:
        case "BCE":
            return ("named_authorship" not in check["notices"]
                    and (check["kind"] == "policy_decision" or (
                        row["kind"] in {"m3", "m3_annex", "account"} and check.get("document_reproduction_notice"))))
        case "Eurostat":
            return check["kind"] == "statistical_release"
        case "Reserva Federal (Junta)":
            return check["kind"] == "speech"
        case _:
            return False


def build_records(by_id, proposal, snapshot, authored):
    snapshot = Snapshot.model_validate(snapshot).model_dump(exclude_none=True)
    docs, claims, decisions = [], [], []
    if set(snapshot["documents"]) != set(authored) or not set(proposal) <= set(authored) <= by_id.keys():
        raise ValueError("reviews must cover the entire M7.A proposal and reviewed replacements")
    reviews = {id: Review.model_validate(item) for id, item in authored.items()}
    replacements = set()
    hashes = set()
    for id in sorted(reviews):
        review = reviews[id]
        row = by_id[id]
        check = snapshot["documents"][id]
        source = row["institution"]
        policy = check.get("rights_policy", snapshot["policies"][source])
        is_original = id in proposal
        is_expansion = row.get("m85_expansion") is True
        if (is_original or is_expansion) and review.replaces or not (is_original or is_expansion) and (
            not review.replaces or review.replaces not in proposal
            or reviews[review.replaces].status != "rejected"
            or by_id[review.replaces]["event"] != row["event"]
            or review.replaces in replacements
        ):
            raise ValueError("invalid replacement link")
        if review.replaces:
            replacements.add(review.replaces)
        check_source(row["original_url"], source)
        if check["id"] != id or check["url"] != row["original_url"] or check["institution"] != source:
            raise ValueError("source check belongs to another document")
        if m7a.stable_id(row["canonical_url"]) != id or not m7a.in_window(row["event"], row["publication_date"]):
            raise ValueError("invalid M7.A identity or event window")
        old_style = check.get("comparison_sha256")
        changed = "not_checked" if not old_style else "same" if old_style == row["content_sha256"] else "changed"
        decisions.append(Decision(
            schema_version="m7b-v1", document_id=id, event=row["event"], institution=source,
            original_url=row["original_url"], m7a_publication_date=row["publication_date"],
            m7a_content_sha256=row["content_sha256"], status=review.status,
            is_original_proposal=is_original, replaces=review.replaces, checked_at=check["checked_at"],
            source_access=check["access"], source_identity=check["identity"], title=row["title"],
            document_type=check.get("kind"), source_payload_sha256=check.get("payload_sha256"),
            normalized_text_sha256=check.get("text_sha256"), source_change=changed,
            notice_flags=check.get("notices", []), content_review=review.content_review,
            content_locator=review.content_locator, rights_status=review.rights_status,
            rights_evidence=review.rights_evidence, rights_url=policy["url"], reason=review.reason,
        ))
        if review.status != "approved":
            if any((review.summary, review.claims, review.reviewed_payload_sha256,
                    review.reviewed_text_sha256, review.reviewed_policy_sha256,
                    review.indicators, review.geography, review.historical_period, review.mechanism)) or review.rights_status == "scoped_derivatives_verified":
                raise ValueError("unapproved entry carries publication content")
            continue
        if not approval_eligible(row, check, policy) or review.rights_status != "scoped_derivatives_verified":
            raise ValueError("approval lacks source-specific rights evidence")
        if (comparable(check["title"]) != comparable(row["title"]) or check["page_date"] not in {
            row["publication_date"], row["publication_evidence"].get("page_date"),
        } or check["checked_at"][:10] < row["publication_date"]):
            raise ValueError("approval does not match title or publication chronology")
        if (check.get("payload_sha256"), check.get("text_sha256"), policy.get("sha256")) != (
            review.reviewed_payload_sha256, review.reviewed_text_sha256, review.reviewed_policy_sha256
        ):
            raise ValueError("source version or policy differs from editorial review")
        if m7a.identity(check_source(check["final_url"], source)) != row["canonical_url"]:
            raise ValueError("approved source destination differs from inventory")
        if (not review.summary or not review.indicators or not review.claims
                or not review.historical_period or not review.geography or not review.mechanism):
            raise ValueError("approved record lacks authored evidence")
        if not review.historical_period.start <= review.historical_period.end:
            raise ValueError("inverted historical period")
        # Future announced periods belong in a typed projection, never an observed claim.
        if review.historical_period.start > row["publication_date"]:
            raise ValueError("historical period begins after publication")
        if check["text_sha256"] in hashes:
            raise ValueError("duplicate normalized content in the approved set")
        hashes.add(check["text_sha256"])
        claim_ids = []
        for claim in review.claims:
            if check["blocks"].get(digest(claim.locator)) != claim.source_block_sha256:
                raise ValueError("claim locator does not match reviewed source block")
            if not set(claim.indicators) <= review.indicators.keys() or len(set(claim.indicators)) != len(claim.indicators):
                raise ValueError("claim indicators must be a unique subset of document indicators")
            period = claim.historical_period or review.historical_period
            if period.start > period.end:
                raise ValueError("inverted claim period")
            if claim.type == "observed" and period.end > row["publication_date"]:
                raise ValueError("observed period ends after publication")
            cid = digest(f"{id}\n{claim.type}\n{comparable(claim.text)}")
            if cid in claim_ids:
                raise ValueError("duplicate claim in document")
            claim_ids.append(cid)
            claims.append(Claim(
                schema_version="m7b-v1", id=cid, document_id=id, text=claim.text, type=claim.type,
                primary_source=row["original_url"], locator=claim.locator,
                source_block_sha256=claim.source_block_sha256, publication_date=row["publication_date"],
                historical_period=period, geography=review.geography, event=row["event"],
                indicators=claim.indicators, qualifiers=claim.qualifiers, review_status="verified_against_source",
            ))
        docs.append(Document(
            schema_version="m7b-v1", id=id, m7a_id=id, title=check["title"], institution=source,
            primary_source=row["original_url"], media_type=check["media_type"], document_type=check["kind"],
            publication_date=row["publication_date"], date_precision="day", reviewed_at=check["checked_at"],
            language="en", event=row["event"], geography=review.geography, historical_period=review.historical_period,
            indicators=review.indicators, mechanism=review.mechanism, summary=review.summary,
            rights={"policy_url": policy["url"], "policy_sha256": policy["sha256"], "basis": policy["basis"],
                    "specific_evidence": review.rights_evidence,
                    "attribution": f'{source}, {check["title"]} ({row["publication_date"]}). MacroPulse AI paraphrase; the publisher does not endorse it.',
                    "redistribution_scope": "attributed_paraphrases_and_structured_facts",
                    "originals_allowed_in_corpus": False, "original_text_chunks_authorized": False},
            source_payload_sha256=check["payload_sha256"], normalized_text_sha256=check["text_sha256"],
            claim_ids=claim_ids,
        ))
    if len(docs) > 60:
        raise ValueError("approved document ceiling exceeded")
    return [sorted(items, key=lambda r: getattr(r, "id", getattr(r, "document_id", "")))
            for items in (docs, claims, decisions)]


def build(snapshot_path, review_path, out):
    by_id, proposal = inventory()
    snapshot = load_json(snapshot_path)
    hashes = snapshot.get("m7a_sha256", {})
    if set(hashes) != {"manifest.json", "candidates.jsonl"}:
        raise ValueError("M7.A inventory hashes are required")
    for name, expected in hashes.items():
        if digest((M7A / name).read_text(encoding="utf-8")) != expected:
            raise ValueError("M7.A input differs from reviewed snapshot")
    if snapshot.get("expansion_sha256") != digest((M7B / "expansion-candidates.jsonl").read_text(encoding="utf-8")):
        raise ValueError("expansion inventory differs from reviewed snapshot")
    records = build_records(by_id, proposal, snapshot, load_json(review_path))
    out.mkdir(parents=True, exist_ok=True)
    for name, items in zip(OUT_TYPES, records, strict=True):
        write_rows(out / f"{name}.jsonl", [r.model_dump(mode="json") for r in items])
    print(f"{len(records[0])} approved; {len(records[1])} claims; {dict(Counter(r.status for r in records[2]))}")


def verify_live(reviewed_path, live_path):
    """Compare source/rights evidence; never adopt a changed snapshot implicitly."""
    reviewed = Snapshot.model_validate(load_json(reviewed_path))
    live = Snapshot.model_validate(load_json(live_path))
    issues = []
    for institution, policy in reviewed.policies.items():
        current = live.policies.get(institution)
        if current != policy and any(
            check.institution == institution and check.rights_policy is None
            for check in reviewed.documents.values()
        ):
            issues.append(f"{institution}: rights policy verification or fingerprint changed")
    for id, check in reviewed.documents.items():
        current = live.documents.get(id)
        policy_changed = check.rights_policy is not None and live.policies.get(check.institution) != check.rights_policy
        if policy_changed or not current or any(getattr(current, field) != getattr(check, field) for field in (
            "access", "identity", "final_url", "payload_sha256", "text_sha256", "page_date",
            "notices", "document_reproduction_notice",
        )) or any(current.blocks.get(key) != digest for key, digest in check.blocks.items()):
            issues.append(f"{id}: document, access or claim block changed")
    for issue in issues:
        print(issue)
    if issues:
        raise ValueError("live snapshot differs; editorial review required")
    print(f"{len(reviewed.documents)} source versions and {len(reviewed.policies)} policy fingerprints match.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("inspect")
    scan.add_argument("--out", type=Path, required=True)
    scan.add_argument("--replacement-id", action="append", default=[])
    scan.add_argument("--id", action="append", default=[])
    generate = sub.add_parser("build")
    generate.add_argument("--snapshot", type=Path, default=M7B / "source-checks.json")
    generate.add_argument("--review", type=Path, default=M7B / "editorial-review.json")
    generate.add_argument("--out", type=Path, required=True)
    verify = sub.add_parser("verify-live")
    verify.add_argument("--reviewed", type=Path, default=M7B / "source-checks.json")
    verify.add_argument("--live", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "inspect":
        inspect(args.out, args.replacement_id, inspect_ids=args.id)
    elif args.command == "build":
        build(args.snapshot, args.review, args.out)
    elif args.command == "verify-live":
        verify_live(args.reviewed, args.live)


if __name__ == "__main__":
    main()
