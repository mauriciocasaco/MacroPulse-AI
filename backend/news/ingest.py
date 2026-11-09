"""Local GKG pilot CLI and transactional, idempotent batch writer."""

from argparse import ArgumentParser
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from zipfile import BadZipFile

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from .assets import METHOD_VERSION, associate
from .gdelt import ADAPTER_VERSION, SOURCE_ID, SOURCE_NAME, TERMS_URL, Batch, Record, read_batch, utc


def batch_summary(batch: Batch) -> dict:
    observations = [record.observed_at for record in batch.records]
    publications = [record.published_at for record in batch.records if record.published_at is not None]
    counts = Counter(link.asset_id for record in batch.records for link in associate(record))
    canonical_count = len({record.canonical_url for record in batch.records})
    return {
        "archive": batch.provenance_ref,
        "archive_sha256": batch.archive_sha256,
        "adapter_version": ADAPTER_VERSION,
        "selected": len(batch.selection),
        "processed": batch.processed,
        "normalized": len(batch.records),
        "provider_duplicates": batch.duplicates,
        "canonical_duplicates_in_batch": len(batch.records) - canonical_count,
        "rejected": batch.rejected,
        "observed_from": min(observations).isoformat() if observations else None,
        "observed_to": max(observations).isoformat() if observations else None,
        "published_from": min(publications).isoformat() if publications else None,
        "published_to": max(publications).isoformat() if publications else None,
        "publication_timestamp_count": len(publications),
        "publication_date_only_count": sum(record.published_on is not None for record in batch.records),
        "associated_assets": dict(sorted(counts.items())),
    }


def write_batch(connection: psycopg.Connection, batch: Batch) -> dict:
    selection_hash = sha256("\n".join(batch.selection).encode("ascii")).hexdigest()
    checkpoint_key = SOURCE_ID, batch.provenance_ref, ADAPTER_VERSION, selection_hash
    with connection.transaction():
        # Serialize GKG batches, including distinct selections from the same archive.
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (SOURCE_ID,))
        checkpoint = connection.execute("""
            SELECT archive_sha256, result FROM news.ingestion_batches
            WHERE (source_id, provenance_ref, adapter_version, selection_sha256) = (%s, %s, %s, %s)
        """, checkpoint_key).fetchone()
        if checkpoint:
            digest, previous = checkpoint
            if digest != batch.archive_sha256:
                raise ValueError("archive differs from completed checkpoint")
            return {**previous, "checkpoint_replay": True}

        connection.execute("""
            INSERT INTO news.sources (id, name, terms_url) VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (SOURCE_ID, SOURCE_NAME, TERMS_URL))
        outcomes = Counter(_write_record(connection, record) for record in batch.records)
        result = {
            **batch_summary(batch),
            "checkpoint_replay": False,
            "new_articles": outcomes["new_article"],
            "new_provider_records": outcomes["new_article"] + outcomes["existing_article"],
            "existing_canonical_urls": outcomes["existing_article"],
            "existing_provider_records": outcomes["existing_record"],
            "provider_conflicts": outcomes["provider_conflict"],
        }
        connection.execute("""
            INSERT INTO news.ingestion_batches
                (source_id, provenance_ref, adapter_version, selection_sha256, archive_sha256, result)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (*checkpoint_key, batch.archive_sha256, Jsonb(result)))
    return result


def _write_record(connection: psycopg.Connection, record: Record) -> str:
    existing = connection.execute("""
        SELECT a.canonical_url FROM news.article_records r JOIN news.articles a ON a.id = r.article_id
        WHERE r.source_id = %s AND r.external_id = %s
    """, (SOURCE_ID, record.external_id)).fetchone()
    if existing:
        return "existing_record" if existing[0] == record.canonical_url else "provider_conflict"

    article = connection.execute("""
        INSERT INTO news.articles (canonical_url, publisher, published_at, published_on)
        VALUES (%s, %s, %s, %s) ON CONFLICT (canonical_url) DO NOTHING RETURNING id
    """, (record.canonical_url, record.publisher, record.published_at, record.published_on)).fetchone()
    outcome = "new_article"
    if article is None:
        outcome = "existing_article"
        article = connection.execute(
            "SELECT id FROM news.articles WHERE canonical_url = %s", (record.canonical_url,),
        ).fetchone()
    article_id = article[0]
    record_id = connection.execute("""
        INSERT INTO news.article_records (
            article_id, source_id, external_id, source_url, retrieved_at, provider_observed_at,
            reported_published_at, reported_published_on, reported_publication_raw, provenance_ref,
            themes, entities, reported_publisher
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        article_id, SOURCE_ID, record.external_id, record.source_url, record.retrieved_at,
        record.observed_at, record.published_at, record.published_on, record.publication_raw,
        record.provenance_ref, list(record.themes), list(record.entities), record.publisher,
    )).fetchone()[0]
    for link in associate(record):
        connection.execute("""
            INSERT INTO news.record_assets (record_id, asset_id, relationship, confidence, evidence, method_version)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (record_id, link.asset_id, link.relationship, link.confidence, link.evidence, METHOD_VERSION))
        connection.execute("""
            INSERT INTO news.article_assets (article_id, asset_id, evidence) VALUES (%s, %s, %s)
            ON CONFLICT (article_id, asset_id) DO NOTHING
        """, (article_id, link.asset_id, link.evidence))
    return outcome


def local_dsn(value: str) -> str:
    try:
        options = conninfo_to_dict(value)
    except psycopg.Error:
        raise ValueError("invalid local PostgreSQL configuration") from None
    host = options.get("host")
    if host not in ("127.0.0.1", "::1") or options.get("hostaddr", host) != host:
        raise ValueError("PostgreSQL must use literal loopback")
    if not options.get("port") or not options.get("dbname") or "service" in options:
        raise ValueError("local PostgreSQL requires an explicit port and database")
    return make_conninfo(value, hostaddr=host)


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--record-id", action="append", required=True)
    parser.add_argument("--retrieved-at", help="Archive acquisition time as an ISO-8601 timestamp with offset")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        retrieved = utc(datetime.fromisoformat(args.retrieved_at)) if args.retrieved_at else datetime.now(UTC)
    except ValueError:
        parser.exit(2, "Invalid archive acquisition timestamp.\n")
    try:
        batch = read_batch(args.archive, args.record_id, retrieved)
        if args.dry_run:
            result = batch_summary(batch)
        else:
            dsn = local_dsn(os.environ.get("MARKETPULSE_NEWS_LOCAL_DSN", ""))
            with psycopg.connect(dsn) as connection:
                result = write_batch(connection, batch)
    except (OSError, ValueError, BadZipFile, psycopg.Error) as exc:
        parser.exit(2, f"GKG batch failed ({type(exc).__name__}).\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
