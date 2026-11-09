"""Reviewed historical-corpus import into the guarded Supabase DEV project."""

from argparse import ArgumentParser
from collections import Counter
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.market_data.dev_import import (
    DEV_PROJECT_REF, DevImportError, _start_read_only_preflight, configured_dev_dsn,
    connect_dev, preflight_dev_news,
)

from . import gdelt
from .assets import associate
from .embeddings import E5Small, vector_literal
from .evaluate_historical_corpus import ASSETS, associations, passages
from .historical_corpus import EXTERNAL_PROSE, ROOT, SNAPSHOTS, SOURCES, WINDOWS, stable_id


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def load_reviewed(root: Path, reviewed_sha256: str):
    raw = (root / "manifest.json").read_bytes()
    if digest(raw) != reviewed_sha256:
        raise ValueError("manifest differs from the reviewed rights and provenance inventory")
    manifest = json.loads(raw)
    counts = Counter(eligible_texts=0, eligible_gkg=0, excluded_rights=0)
    reviewed = []
    seen = set()
    for item in manifest["documents"] + manifest["metadata_only"]:
        url = item.get("canonical_url", item.get("url"))
        event = item["event"]
        if event not in WINDOWS or stable_id(url) != item["id"] or url in seen:
            raise ValueError("invalid event, identity or duplicate canonical URL")
        seen.add(url)
        rights = item["rights"]
        if rights.get("external_llm") != "prohibited by pilot scope":
            raise ValueError("unexpected external-processing policy")
        if item["type"] == "discovery_metadata":
            metadata = {key: value for key, value in item.items() if key != "sha256"}
            if digest(json.dumps(metadata, sort_keys=True).encode()) != item["sha256"]:
                raise ValueError("GKG metadata hash mismatch")
            archive = gdelt.ARCHIVE_ROOT + SNAPSHOTS[event] + ".gkg.csv.zip"
            if (rights.get("basis") != gdelt.TERMS_URL or rights.get("storage") != "metadata only"
                    or rights.get("embedding") is not False or item["source_name"] != gdelt.SOURCE_NAME
                    or item["title"] is not None or item["provenance_ref"] != archive + "#" + item["external_id"]
                    or gdelt.canonicalize_url(item["source_url"]) != url
                    or not re.fullmatch(r"[0-9a-f]{64}", item["archive_sha256"])):
                raise ValueError("invalid GKG rights or archive provenance")
            counts["eligible_gkg"] += 1
            reviewed.append(item)
            continue

        source = item.get("source")
        policy = SOURCES.get(source)
        address = urlsplit(url)
        valid_path = {
            "eia": ("www.eia.gov", r"/todayinenergy/detail\.php", r"id=\d+"),
            "fed": ("www.federalreserve.gov", r"/(?:newsevents/speech/[a-z0-9]+|publications/[a-z0-9-]+)\.htm", ""),
        }.get(source)
        if (policy is None or valid_path is None or rights.get("basis") != policy[1]
                or item.get("source_name") != policy[0] or item.get("extractor_version") != 2
                or rights.get("embedding") is not True or rights.get("storage") != "institutional prose only"
                or rights.get("redistribution") != "attribute agency; excluded assets and third-party material"
                or address.scheme != "https" or address.netloc != valid_path[0] or address.fragment
                or not re.fullmatch(valid_path[1], address.path, re.I)
                or not re.fullmatch(valid_path[2], address.query)):
            counts["excluded_rights"] += 1
            continue
        saved = json.loads((root / (item["id"] + ".json")).read_bytes())
        text = saved.pop("text")
        if saved != item or digest(text.encode()) != item["sha256"]:
            raise ValueError("institutional document hash or provenance mismatch")
        day = date.fromisoformat(item["day"])
        if (not WINDOWS[event][0] <= day < WINDOWS[event][1]
                or day > gdelt.utc(datetime.fromisoformat(item["retrieved_at"])).date()
                or item["publication_precision"] != "day" or not item["publication_evidence"]
                or EXTERNAL_PROSE.search(text)):
            raise ValueError("institutional publication evidence is invalid")
        counts["eligible_texts"] += 1
        reviewed.append({**item, "text": text})
    return reviewed, counts


def rows(connection, table, key, expected, *, write, returning="id"):
    """Check immutable fields against typed PostgreSQL rows and insert only missing keys."""
    if not expected:
        return {}
    columns = tuple(expected[0])
    relation = sql.Identifier("news", table)
    def identifiers(names):
        return sql.SQL(", ").join(map(sql.Identifier, names))

    document = Jsonb(expected, dumps=lambda value: json.dumps(value, default=str))
    payload = sql.SQL("jsonb_populate_recordset(NULL::{}, %s::jsonb)").format(relation)
    if write:
        insert = sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO NOTHING").format(
            relation, identifiers(columns), identifiers(columns), payload, identifiers(key),
        )
        connection.execute(insert, (document,))
    join = sql.SQL(" AND ").join(sql.SQL("stored.{} = incoming.{}").format(
        sql.Identifier(name), sql.Identifier(name),
    ) for name in key)
    comparisons = [
        sql.SQL("stored.{0} IS NOT DISTINCT FROM incoming.{0}").format(sql.Identifier(name))
        for name in columns if name not in key and name != "embedding"
    ]
    if "embedding" in columns:
        comparisons.append(sql.SQL("(stored.embedding OPERATOR(extensions.<=>) incoming.embedding) < 0.000001"))
    output = sql.SQL(", ").join(sql.SQL("stored.{}").format(sql.Identifier(name)) for name in (*key, returning))
    query = sql.SQL("SELECT {}, {} FROM {} incoming JOIN {} stored ON {}").format(
        output, sql.SQL(" AND ").join(comparisons) if comparisons else sql.SQL("true"),
        payload, relation, join,
    )
    found = {}
    for *identity, result, matches in connection.execute(query, (document,)):
        if not matches:
            raise ValueError(f"conflicting row in news.{table}")
        found[tuple(identity)] = result
    return found


def source_id(item):
    return item.get("source", gdelt.SOURCE_ID)


def article_row(item):
    metadata = "text" not in item
    return {
        "canonical_url": item["canonical_url"] if metadata else item["url"],
        "publisher": item["publisher"] if metadata else item["source_name"],
        "title": item["title"],
        "published_on": None if metadata else item["day"],
        "published_at": None,
    }


def record_row(item, article, index, content):
    metadata = content is None
    url = item["source_url"] if metadata else item["url"]
    themes = [f'marketpulse:event:{item["event"]}']
    if metadata:
        themes.extend(theme for theme in item["themes"] if not theme.casefold().startswith("marketpulse:"))
    return {
        "article_id": article, "source_id": source_id(item),
        "external_id": item["external_id"] if metadata else f'{item["id"]}:{index}',
        "source_url": url, "retrieved_at": item["retrieved_at"],
        "provider_observed_at": item["observed_at"] if metadata else None,
        # GKG publication tags remain raw, unverified metadata.
        "reported_published_at": None, "reported_published_on": None if metadata else item["day"],
        "reported_publication_raw": item["publication_raw"] if metadata else item["publication_evidence"],
        "provenance_ref": (f'{item["provenance_ref"]};sha256={item["archive_sha256"]}' if metadata
                           else f'{url}#marketpulse-passage-{index};sha256={item["sha256"]}'),
        "content_kind": "none" if metadata else "excerpt", "content": content,
        "storage_permission": "metadata_only" if metadata else "excerpt",
        "public_display_allowed": not metadata, "embedding_allowed": not metadata,
        "rights_basis": item["rights"]["basis"],
        "themes": themes,
        "entities": item["entities"] if metadata else [], "reported_publisher": article_row(item)["publisher"],
        "is_synthetic": False, "event_at": None, "event_on": None,
    }


def persist(connection, prepared, spec, *, write):
    counts = Counter()
    sources = {source_id(item): {"id": source_id(item), "name": item["source_name"],
                                "terms_url": item["rights"]["basis"]} for item, _, _ in prepared}
    rows(connection, "sources", ("id",), list(sources.values()), write=write)
    articles = rows(connection, "articles", ("canonical_url",),
                    [article_row(item) for item, _, _ in prepared], write=write)
    pending_records = []
    for item, records, links in prepared:
        article = articles.get((article_row(item)["canonical_url"],))
        if article is None:
            continue
        counts["documents" if "text" in item else "metadata"] += 1
        for index, (content, vector) in enumerate(records):
            pending_records.append((record_row(item, article, index, content), vector, links))

    recorded = rows(connection, "article_records", ("source_id", "external_id"),
                    [record for record, _, _ in pending_records], write=write)
    pending_links, vectors = [], []
    for data, vector, links in pending_records:
        record = recorded.get((data["source_id"], data["external_id"]))
        if record is None:
            continue
        counts["metadata_records" if vector is None else "passages"] += 1
        pending_links.extend({
            "record_id": record, "asset_id": ASSETS[asset], "relationship": relation,
            "confidence": "medium", "evidence": evidence, "method_version": "historical-corpus-v1",
        } for asset, relation, evidence in links)
        if vector is not None:
            vectors.append({
                "record_id": record, "model_id": spec.model_id, "model_revision": spec.revision,
                "dimensions": spec.dimensions, "content_sha256": digest(data["content"].encode()), "embedding": vector,
            })

    counts["asset_links"] = len(rows(connection, "record_assets", ("record_id", "asset_id"),
                                     pending_links, write=write, returning="record_id"))
    if vectors:
        rows(connection, "embedding_models", ("id", "revision"), [
            {"id": spec.model_id, "revision": spec.revision, "dimensions": spec.dimensions},
        ], write=write)
    counts["embeddings"] = len(rows(connection, "record_embeddings", ("record_id", "model_id", "model_revision"),
                                     vectors, write=write, returning="record_id"))
    return +counts


def prepare(items, model):
    for item in items:
        if "text" in item:
            links = [(symbol, relation, f"Subject phrase: {term}") for symbol, relation, term in associations(item)]
            records = [(text, vector_literal(model.embed(text, query=False), model.spec))
                       for text in passages(item, model.tokenizer)]
        else:
            gdelt_record = gdelt.Record(
                external_id=item["external_id"], source_url=item["source_url"], canonical_url=item["canonical_url"],
                publisher=item["publisher"], observed_at=datetime.fromisoformat(item["observed_at"]),
                retrieved_at=datetime.fromisoformat(item["retrieved_at"]), provenance_ref=item["provenance_ref"],
                published_at=None, published_on=None, publication_raw=item["publication_raw"],
                themes=tuple(item["themes"]), entities=tuple(item["entities"]),
            )
            symbols = {asset: symbol for symbol, asset in ASSETS.items()}
            links = [(symbols[link.asset_id], link.relationship, link.evidence) for link in associate(gdelt_record)]
            records = [(None, None)]
        yield item, records, links


def check_write_access(connection):
    ready = connection.execute("""
        SELECT has_schema_privilege('news', 'USAGE')
           AND bool_and(has_table_privilege(format('news.%I', name), 'SELECT')
                        AND has_table_privilege(format('news.%I', name), 'INSERT'))
        FROM unnest(ARRAY['sources','articles','article_records','record_assets',
                          'embedding_models','record_embeddings']) AS name
    """).fetchone()[0]
    if not ready:
        raise DevImportError("the DEV role cannot write the required news tables")


def main():
    parser = ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--verify-dev", action="store_true")
    mode.add_argument("--apply-dev", action="store_true")
    parser.add_argument("--reviewed-manifest-sha256", required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--confirm-dev-write")
    args = parser.parse_args()
    if args.confirm_dev_write != (DEV_PROJECT_REF if args.apply_dev else None):
        parser.error("DEV writes require --confirm-dev-write with the authorized project")
    try:
        items, rights = load_reviewed(ROOT, args.reviewed_manifest_sha256)
        os.environ["HF_HUB_OFFLINE"] = "1"
        import torch
        torch.set_num_threads(2)
        model = E5Small(args.model_cache)
        prepared = list(prepare(items, model))
        planned = Counter(
            documents=rights["eligible_texts"], metadata=rights["eligible_gkg"],
            metadata_records=rights["eligible_gkg"], passages=sum(len(records) for item, records, _ in prepared if "text" in item),
            asset_links=sum(len(records) * len(links) for _, records, links in prepared),
        )
        planned["embeddings"] = planned["passages"]
        result = {"rights": dict(rights), "planned": dict(planned)}
        if not args.dry_run:
            dsn = configured_dev_dsn()
            if preflight_dev_news(dsn).pending:
                raise DevImportError("all news migrations must be applied")
            with connect_dev(dsn) as connection:
                _start_read_only_preflight(connection)
                connection.execute("SET LOCAL statement_timeout = '30s'")
                if args.apply_dev:
                    check_write_access(connection)
                before = persist(connection, prepared, model.spec, write=False)
            result["existing"] = dict(before)
            if args.apply_dev and before != planned:
                with connect_dev(dsn) as connection:
                    connection.execute("SET LOCAL lock_timeout = '3s'")
                    connection.execute("SET LOCAL statement_timeout = '30s'")
                    connection.execute("LOCK TABLE news.sources, news.articles, news.article_records, "
                                       "news.record_assets, news.embedding_models, news.record_embeddings "
                                       "IN SHARE ROW EXCLUSIVE MODE")
                    before = persist(connection, prepared, model.spec, write=False)
                    written = persist(connection, prepared, model.spec, write=True)
                    if written != planned:
                        raise ValueError("write verification does not match dry-run")
                with connect_dev(dsn) as connection:
                    _start_read_only_preflight(connection)
                    connection.execute("SET LOCAL statement_timeout = '30s'")
                    after = persist(connection, prepared, model.spec, write=False)
                if after != planned:
                    raise ValueError("committed rows do not match the reviewed package")
                result["verified"] = dict(after)
                result["added"] = dict(after - before)
            elif before == planned:
                result["verified"] = dict(before)
                result["added"] = {}
                result["replayed"] = args.apply_dev
            else:
                result["missing"] = dict(planned - before)
                print(json.dumps(result, sort_keys=True), file=sys.stderr)
                parser.exit(1, "Historical corpus verification found missing rows.\n")
        print(json.dumps(result, sort_keys=True))
    except (DevImportError, OSError, ValueError, KeyError, TypeError, RuntimeError, psycopg.Error) as error:
        parser.exit(1, f"Historical corpus operation failed ({type(error).__name__}).\n")


if __name__ == "__main__":
    main()
