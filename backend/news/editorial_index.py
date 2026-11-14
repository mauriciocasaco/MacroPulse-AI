"""Build and search an offline E5 index of explicitly authorized editorial claims."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from .embeddings import E5Small, vector_literal


def digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def timestamp(value):
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None:
        raise ValueError("explicit timestamp offset required")
    return instant


def url_valid(value):
    url = urlsplit(value)
    return (url.scheme == "https" and bool(url.hostname) and
            not url.username and not url.password and not any(c.isspace() for c in value))


def identified(value):
    return (bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{2,99}", value))
            and value.casefold() not in {"auto", "system", "pipeline", "model", "agent", "codex", "assistant"})


def check_rights(rights, use, case_version, source_version, at):
    if (rights["use"], rights["decision"], rights["scope"], rights["case_version"],
        rights["source_fingerprint"]) != (use, "allowed", "original_summary", case_version, source_version):
        raise ValueError("permission does not cover the exact editorial input")
    if (not identified(rights["rights_actor"]) or not rights["basis"].strip()
            or not rights["conditions"].strip() or not url_valid(rights["basis_url"])
            or not url_valid(rights["terms_url"])):
        raise ValueError("rights decision lacks evidence")
    if digest({k: v for k, v in rights.items() if k != "rights_version"}) != rights["rights_version"]:
        raise ValueError("rights fingerprint mismatch")
    decided, effective = timestamp(rights["decided_at"]), timestamp(rights["effective_at"])
    if effective < decided or effective > at:
        raise ValueError("rights decision is not yet effective")
    if rights["revoked_at"] is not None or (rights["expires_at"] and timestamp(rights["expires_at"]) <= at):
        return False
    return True


def eligible(batch, at):
    """Validate the trusted operator export. Drafts are never embedding inputs."""
    prepared_at = timestamp(batch["as_of"])
    if batch["schema_version"] != 1 or batch["mode"] != "dry_run" or prepared_at > at:
        raise ValueError("unsupported or future preparation batch")
    approved = {}
    for entry in batch["approved_staging"]:
        claim = entry["claim"]
        key = claim["claim_id"], claim["case_version"]
        if key in approved:
            raise ValueError("duplicate approved claim version")
        approved[key] = entry
    seen = set()
    rows = []
    for item in batch["approved_embedding_staging"]:
        key = item["claim_id"], item["case_version"]
        if key in seen or key not in approved:
            raise ValueError("duplicate or unapproved embedding input")
        seen.add(key)
        entry = approved[key]
        claim, source, review = entry["claim"], entry["source"], entry["review"]
        source_version = source["source_fingerprint"]
        if digest({k: v for k, v in source.items() if k != "source_fingerprint"}) != source_version:
            raise ValueError("source fingerprint mismatch")
        if any(candidate != source_version for candidate in
               (item["source_fingerprint"], claim["source_fingerprint"], review["source_fingerprint"])):
            raise ValueError("source version mismatch")
        text = item["text"]
        if (not text.strip() or len(text) > 600 or claim["summary"] != text
                or claim["summary_sha256"] != item["content_sha256"]
                or sha256(text.encode("utf-8")).hexdigest() != item["content_sha256"]):
            raise ValueError("embedding text does not match the approved original")
        factual, embedding = entry["rights"], item["rights"]
        if (item["review"] != review or review["decision"] != "approved"
                or review["factual_rights_version"] != factual["rights_version"]
                or review["case_version"] != key[1] or not identified(review["reviewer_id"])
                or not review["reason"].strip() or not url_valid(review["review_evidence_url"])
                or item["rights_version"] != embedding["rights_version"]):
            raise ValueError("review or permission version mismatch")
        current_factual = check_rights(factual, "factual_public", key[1], source_version, at)
        current_embedding = check_rights(embedding, "embedding", key[1], source_version, at)
        reviewed = timestamp(review["decided_at"])
        observed = timestamp(source["observed_at"])
        if (not url_valid(source["evidence_url"])
                or not max(observed, timestamp(factual["effective_at"])) <= reviewed <= prepared_at
                or timestamp(embedding["effective_at"]) > prepared_at):
            raise ValueError("review chronology is inconsistent")
        if current_factual and current_embedding:
            rows.append((item, claim, source, max(reviewed, timestamp(embedding["effective_at"]))))
    return rows


def load_batch(path, expected):
    data = path.read_bytes()
    if sha256(data).hexdigest() != expected:
        raise ValueError("batch checksum mismatch")
    return json.loads(data)


def build_index(rows, model):
    entries = []
    for item, claim, source, available_at in rows:
        vector = json.loads(vector_literal(model.embed(item["text"], query=False), model.spec))
        entries.append({"claim_id": item["claim_id"], "case_version": item["case_version"],
                        "content_sha256": item["content_sha256"], "rights_version": item["rights_version"],
                        "source_fingerprint": item["source_fingerprint"], "event": claim["event"],
                        "indicator": claim["indicator"], "published_on": source["published_on"],
                        "available_at": available_at.isoformat(), "vector": vector})
    return {"model": vars(model.spec), "entries": entries}


def search_index(index, rows, model, query, event, indicator, limit, *, cutoff=None):
    if index["model"] != vars(model.spec):
        raise ValueError("index model does not match the requested revision")
    current = {(item["claim_id"], item["case_version"]): (item, claim, source, available)
               for item, claim, source, available in rows}
    matches = []
    for row in index["entries"]:
        key = row["claim_id"], row["case_version"]
        if key not in current:
            continue
        item, claim, source, available = current[key]
        if cutoff is not None and available > cutoff:
            continue
        if claim["event"] != event or claim["indicator"] != indicator:
            continue
        if any(row[field] != item[field] for field in ("rights_version", "content_sha256", "source_fingerprint")):
            continue
        matches.append((row, claim, source))
    if not matches:
        return []
    vector = json.loads(vector_literal(model.embed(query, query=True), model.spec))
    results = []
    for row, claim, source in matches:
        passage = json.loads(vector_literal(row["vector"], model.spec))
        score = math.fsum(a * b for a, b in zip(vector, passage, strict=True))
        results.append({"claim_id": row["claim_id"], "case_version": row["case_version"],
                        "score": score, "summary": claim["summary"], "url": source["evidence_url"]})
    return sorted(results, key=lambda row: (-row["score"], row["claim_id"], row["case_version"]))[:limit]


def write_index(path, index):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
    try:
        temp.write_text(json.dumps(index, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--batch-sha256", required=True, help="Checksum from the trusted local preparation process")
    parser.add_argument("--model-cache", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--event", required=True, choices=("covid", "ukraine", "genai"))
    search.add_argument("--indicator", required=True, choices=("eur-usd","deposit-rate","m3-stock","real-gdp-qoq","hicp-yoy"))
    search.add_argument("--cutoff", help="Optional ISO timestamp for historical evidence availability")
    search.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    args = parser.parse_args()
    if args.index.resolve() == args.batch.resolve() or args.index.suffix != ".json":
        parser.error("index must be a separate JSON output path")
    try:
        rows = eligible(load_batch(args.batch, args.batch_sha256), datetime.now(timezone.utc))
        if args.command == "build":
            index = build_index(rows, E5Small(args.model_cache)) if rows else {"model": vars(E5Small.spec), "entries": []}
            write_index(args.index, index)
            print(json.dumps({"eligible": len(rows), "indexed": len(index["entries"])}))
        elif rows:
            index = json.loads(args.index.read_text(encoding="utf-8"))
            cutoff = timestamp(args.cutoff) if args.cutoff else None
            results = search_index(index, rows, E5Small(args.model_cache), args.query,
                                   args.event, args.indicator, args.limit, cutoff=cutoff)
            print(json.dumps(results, ensure_ascii=True))
        else:
            print("[]")
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
