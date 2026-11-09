"""Evaluate the real historical corpus through Phase 3.6 in a temporary loopback database."""
import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, time
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from statistics import mean
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.market_data.catalog import ASSETS as MARKET_ASSETS, ASSET_SOURCES
from backend.market_data.dev_import import (
    DevImportError, _start_read_only_preflight, configured_dev_dsn, connect_dev, preflight_dev_news,
)
from backend.market_data.migrations import apply_migrations

from .embeddings import E5Small, embed_record
from .evaluation import Judgment, evaluate as retrieval_metrics
from .historical_corpus import ROOT, SOURCES, WINDOWS, write_json
from .research_judgments import CONTROLS, DIRECT, QUESTIONS, reviewed_sources
from .retrieval import Search, search

ASSETS = {item.symbol: key for key, item in ASSET_SOURCES.items()}
# Scope by observable subject terms. Judgments below use the economic argument, independently.
ASSOCIATIONS = {
    "GC=F": ("gold futures|comex gold", "gold price|gold market|precious metals", "real (?:interest )?rates|safe haven"),
    "CL=F": ("west texas intermediate|\\bwti\\b", "crude oil|petroleum", "energy prices|energy costs|power demand"),
    "^GSPC": ("s&p 500|standard & poor.s 500", "stock prices|equity prices|stock market|equity valuations", "discount rate|corporate earnings|productivity"),
    "VT": ("vanguard total world", "global equit|global stocks|global stock market", "global (?:economic )?growth|global economy|world economy|global inflation|economic activity abroad"),
    "VWO": ("vanguard ftse emerging", "emerging market|emerging econom|\\bemes\\b", "capital outflows|dollar funding|china"),
    "BTC-USD": ("\\bbitcoin\\b", "cryptocurrenc|crypto.assets", "digital assets"),
}


def associations(doc):
    prose = doc["title"] + " " + doc["text"]
    for symbol, patterns in ASSOCIATIONS.items():
        for relationship, pattern in zip(("direct", "thematic", "macro"), patterns, strict=True):
            if match := re.search(pattern, prose, re.I):
                yield symbol, relationship, match.group()
                break


def passages(doc, tokenizer):
    # Encode only local prose and retain every token, in deterministic non-overlapping passages.
    text = doc["title"] + "\n\n" + doc["text"]
    tokens = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False, verbose=False)
    offsets = tokens["offset_mapping"]
    for start in range(0, len(offsets), 440):
        end = min(start + 439, len(offsets) - 1)
        yield text[offsets[start][0]:offsets[end][1]]


@contextmanager
def disposable(port):
    base = make_conninfo(host="127.0.0.1", hostaddr="127.0.0.1", port=port, dbname="postgres",
                         user="postgres", sslmode="disable", gssencmode="disable", connect_timeout=5)
    name = "marketpulse_corpus_" + uuid4().hex[:16]
    identifier = sql.Identifier(name)
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(identifier))
        try:
            dsn = make_conninfo(base, dbname=name)
            apply_migrations(dsn)
            with psycopg.connect(dsn) as connection:
                migrations = ROOT.parents[1] / "supabase" / "migrations"
                for migration in sorted(migrations.glob("*_news*.sql")):
                    connection.execute(migration.read_text(encoding="utf-8"))
                connection.commit()
                yield connection
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(identifier))


def persist(connection, docs, model):
    for source, (name, policy) in SOURCES.items():
        connection.execute("INSERT INTO news.sources VALUES (%s, %s, %s)", (source, name, policy))
    count = 0
    for doc in docs:
        article, = connection.execute("""
            INSERT INTO news.articles (canonical_url, publisher, title, published_on)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (doc["url"], doc["source_name"], doc["title"], doc["day"])).fetchone()
        links = list(associations(doc))
        doc["asset_associations"] = [
            {"asset": symbol, "relation": relationship, "evidence": term} for symbol, relationship, term in links
        ]
        for index, passage in enumerate(passages(doc, model.tokenizer)):
            record, = connection.execute("""
                INSERT INTO news.article_records (
                    article_id, source_id, external_id, source_url, retrieved_at, reported_published_on,
                    reported_publication_raw, provenance_ref, content_kind, content, storage_permission,
                    public_display_allowed, embedding_allowed, rights_basis
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'excerpt', %s, 'excerpt', true, true, %s)
                RETURNING id
            """, (article, doc["source"], f'{doc["id"]}:{index}', doc["url"], doc["retrieved_at"], doc["day"],
                  doc["publication_evidence"], f'{doc["url"]}#marketpulse-passage-{index}', passage,
                  doc["rights"]["basis"])).fetchone()
            for symbol, relationship, term in links:
                connection.execute("""
                    INSERT INTO news.record_assets (record_id, asset_id, relationship, confidence, evidence, method_version)
                    VALUES (%s, %s, %s, 'medium', %s, 'institutional-prose-v1')
                """, (record, ASSETS[symbol], relationship, f"Subject phrase: {term}"))
            embed_record(connection, record, model)
            count += 1
    connection.commit()
    return count


def price_observation(connection, symbol, start, end):
    bars = connection.execute("""
        SELECT b.bar_date, b.close, b.low
        FROM market_data.price_bars b
        JOIN market_data.asset_sources s ON s.id = b.asset_source_id
        WHERE s.asset_id = %s AND s.source_id = 'yahoo_finance' AND b.interval = '1d'
          AND b.ohlc_basis = 'provider_unadjusted' AND b.bar_date >= %s AND b.bar_date < %s
        ORDER BY b.bar_date
    """, (ASSETS[symbol], start.date(), end.date())).fetchall()
    if not bars:
        return {"bars": 0}
    closes = [(day, close) for day, close, _ in bars]
    first, last = closes[0], closes[-1]
    low = min(closes, key=lambda bar: bar[1])
    high = max(closes, key=lambda bar: bar[1])
    positive_prices = all(close > 0 and (intraday_low is None or intraday_low > 0)
                          for _, close, intraday_low in bars)
    return {
        "bars": len(bars), "source": "Yahoo Finance", "basis": "provider_unadjusted",
        "unit": MARKET_ASSETS[ASSETS[symbol]].economic_unit,
        "first": first, "last": last, "minimum_close": low, "maximum_close": high,
        "close_change": last[1] - first[1] if len(bars) > 1 else None,
        "close_change_pct": (last[1] / first[1] - 1) * 100 if positive_prices and len(bars) > 1 else None,
    }


def evaluate(connection, docs, model, *, dev=False):
    rows = []
    for event, by_symbol in QUESTIONS.items():
        start, end = (datetime.combine(day, time.min, UTC) for day in WINDOWS[event])
        event_docs = [doc for doc in docs if doc["event"] == event]
        for symbol, (question, reference, reason) in by_symbol.items():
            def resolve(suffixes):
                return [doc["url"] for doc in event_docs if any(doc["url"].endswith(s) for s in suffixes)]

            identities = {(item.source_id, item.url, item.published_on.isoformat())
                          for item in reviewed_sources(event, symbol)}
            relevant = [doc["url"] for doc in event_docs if (doc["source"], doc["url"], doc["day"]) in identities]
            controls = resolve(CONTROLS[event])
            request = Search(question, (symbol,), start, end, tuple(SOURCES), window_basis="publication_day")
            if dev:
                request = replace(request, required_theme=f"marketpulse:event:{event}")
            results = {}
            for mode, provider in (("lexical", None), ("hybrid", model)):
                items = search(connection, request, provider)["evidence"]
                ranked = [
                    {"url": item["original_url"], "rank": rank, "date": item["published_on"],
                     "cosine": item["cosine_similarity"], "score": item["score"],
                     "source": item["source_name"], "evidence_id": item["evidence_id"],
                     "provenance_ref": item["provenance_ref"],
                     "judgment": ("relevant" if item["original_url"] in relevant else
                                  "control" if item["original_url"] in controls else "unjudged")}
                    for rank, item in enumerate(items, 1)
                ]
                if any(not start.date() <= date.fromisoformat(item["date"]) < end.date() for item in ranked):
                    raise ValueError("retrieval returned a document outside its event window")
                metrics = {}
                if reference and len(relevant) == len(reference):
                    judgment = Judgment(frozenset(relevant), tuple(item["url"] for item in ranked))
                    for k in (1, 3, 5):
                        result = retrieval_metrics([judgment], k=k)
                        metrics[k] = {"hit": result["hit_at_k"], "recall": result["recall_at_k"],
                                      "mrr": result["mrr_at_k"], "precision_lower_bound": result["precision_at_k"]}
                results[mode] = {"ranked": ranked, "metrics": metrics}
            rows.append({
                "event": event, "asset": symbol, "question": question, "reason": reason,
                "grade": ("directa" if (event, symbol) in DIRECT else "contextual") if relevant else "insuficiente",
                "relevant": relevant, "controls": controls,
                "missing_references": [s for s in reference if not any(url.endswith(s) for url in relevant)],
                "results": results,
            })
            if dev:
                cutoff_items = search(connection, replace(request, as_of=end))["evidence"]
                if cutoff_items:
                    raise ValueError("date-only pilot text was returned for a strict historical cutoff")
                rows[-1]["as_of_end_eligible"] = len(cutoff_items)
                rows[-1]["prices"] = price_observation(connection, symbol, start, end)
    return rows


def write_review(docs, rows, aggregates, manifest, vectors, *, path=ROOT / "revision-humana.md"):
    lines = [
        "# Piloto de corpus histórico de MarketPulse AI",
        "",
        f"{manifest['unique_urls_discovered']} URL únicas descubiertas, {len(docs)} textos autorizados, "
        f"{len(manifest['metadata_only'])} registros de solo metadatos y {vectors} pasajes con E5.",
        "",
        "Las ventanas corresponden a días de publicación. La evaluación es retrospectiva y usa las versiones recuperadas. "
        "Las citas permiten rastrear los originales. Los gráficos, tablas y notas de fuente se excluyeron de los pasajes.",
        "",
        "## Matriz de 18 escenarios",
        "",
        "| Evento | Activo | Juicio | Mecanismo y límite | Referencias positivas | Controles recuperados |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        refs = ", ".join(f"[{n}]({url})" for n, url in enumerate(row["relevant"], 1)) or "—"
        controls = sum(item["judgment"] == "control" for item in row["results"]["hybrid"]["ranked"])
        lines.append(f"| {row['event']} | `{row['asset']}` | {row['grade']} | {row['reason']} | {refs} | {controls} |")
    if rows and "prices" in rows[0]:
        lines.extend(["", "## Precios retrospectivos en PostgreSQL DEV", "",
                      "Cierres diarios sin ajustar de Yahoo Finance. Cambio entre primer y último cierre. "
                      "Se omite el cambio porcentual cuando hay un cierre o un mínimo intradía no positivo. "
                      "El corte estricto al final de cada ventana excluyó toda la prosa fechada solo por día.",
                      "", "| Evento | Activo | Barras | Primer cierre | Último cierre | Mínimo | Cambio | Cambio % | Unidad |",
                      "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        for row in rows:
            price = row["prices"]
            if not price["bars"]:
                lines.append(f"| {row['event']} | `{row['asset']}` | 0 | — | — | — | — | — | — |")
                continue
            pct = price["close_change_pct"]
            pct = f"{pct:.2f}%" if pct is not None else "—"
            change = price["close_change"]
            change = f"{change:.2f}" if change is not None else "—"
            lines.append(
                f"| {row['event']} | `{row['asset']}` | {price['bars']} | {price['first'][1]:.2f} | "
                f"{price['last'][1]:.2f} | {price['minimum_close'][1]:.2f} | {change} | "
                f"{pct} | {price['unit']} |"
            )
    lines.extend(["", "## Recuperación", "", "| Modo | K | Casos | Hit@K | Recall@K | MRR@K | Precisión mínima@K |",
                  "| --- | --- | --- | --- | --- | --- | --- |"])
    for mode, values in aggregates.items():
        for k, result in values.items():
            lines.append(f"| {mode} | {k} | {result['n']} | {result['hit']:.3f} | {result['recall']:.3f} | "
                         f"{result['mrr']:.3f} | {result['precision_lower_bound']:.3f} |")
    lines.extend(["", "Cada métrica promedia las preguntas con todas sus referencias disponibles. "
                  "Los resultados sin juicio quedan en el ranking. Los controles pueden quedar fuera del alcance por activo "
                  "y los escenarios insuficientes se revisan cualitativamente.", "", "## Documentos para revisión", ""])
    urls = list(dict.fromkeys(url for row in rows for url in row["relevant"]))
    for row in rows:
        urls.extend(url for url in row["controls"] if url not in urls)
    for url in list(dict.fromkeys(urls))[:13]:
        doc = next(item for item in docs if item["url"] == url)
        query_refs = [row for row in rows if url in row["relevant"]]
        reason = " ".join(dict.fromkeys(row["reason"] for row in query_refs)) or "Control temático del período."
        rank = [f"{row['asset']} #{item['rank']}" + (f" (coseno {item['cosine']:.3f})" if item["cosine"] is not None else "")
                for row in rows for item in row["results"]["hybrid"]["ranked"]
                if item["url"] == url]
        subject = query_refs[0]["asset"] if query_refs else ""
        terms = ASSOCIATIONS.get(subject, ())
        chunks = doc["text"].split("\n\n")
        excerpt = next((p for p in chunks if any(re.search(term, p, re.I) for term in terms)), chunks[0])
        short = " ".join(excerpt.split()[:16])
        assets = ", ".join(f"`{link['asset']}` ({link['relation']})" for link in doc["asset_associations"]) or "—"
        lines.extend([
            f"### {doc['title']}", "",
            f"{doc['source_name']} · {doc['day']} · {doc['event']}. Activos asociados {assets}. [Original]({url})", "",
            f"> {short}…", "", reason, "",
            f"Texto institucional elegible para almacenamiento y embeddings locales. [Política]({doc['rights']['basis']})", "",
            "Ranking híbrido " + (", ".join(rank) if rank else "sin aparición en los cinco primeros") + ".", "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", type=int, help="port of a disposable PostgreSQL on 127.0.0.1")
    mode.add_argument("--dev-read-only", action="store_true", help="evaluate the reviewed corpus already in Supabase DEV")
    parser.add_argument("--model-cache", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    import torch

    torch.set_num_threads(2)
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    docs = []
    for meta in manifest["documents"]:
        saved = json.loads((ROOT / (meta["id"] + ".json")).read_text(encoding="utf-8"))
        if (not meta["rights"]["embedding"] or meta["rights"]["basis"] != SOURCES[meta["source"]][1]
                or meta["sha256"] != sha256(saved["text"].encode()).hexdigest()):
            raise ValueError("manifest rights or content hash mismatch")
        docs.append({**meta, "text": saved["text"]})
    model = E5Small(args.model_cache)
    if args.dev_read_only:
        try:
            dsn = configured_dev_dsn()
            if preflight_dev_news(dsn).pending:
                raise DevImportError("DEV news schema is incomplete")
            with connect_dev(dsn) as connection:
                _start_read_only_preflight(connection)
                connection.execute("SET LOCAL statement_timeout = '30s'")
                vector_count = connection.execute("SELECT count(*) FROM news.record_embeddings").fetchone()[0]
                rows = evaluate(connection, docs, model, dev=True)
        except (DevImportError, ValueError, psycopg.Error) as error:
            parser.exit(1, f"DEV evaluation failed ({type(error).__name__}).\n")
        for doc in docs:
            doc["asset_associations"] = [
                {"asset": symbol, "relation": relation, "evidence": term} for symbol, relation, term in associations(doc)
            ]
    else:
        with disposable(args.port) as connection:
            vector_count = persist(connection, docs, model)
            rows = evaluate(connection, docs, model)
    aggregates = {}
    for mode in ("lexical", "hybrid"):
        aggregates[mode] = {}
        for k in (1, 3, 5):
            cases = [row["results"][mode]["metrics"][k] for row in rows if k in row["results"][mode]["metrics"]]
            aggregates[mode][k] = {"n": len(cases), **{
                key: mean(case[key] for case in cases) if cases else 0
                for key in ("hit", "recall", "mrr", "precision_lower_bound")
            }}
    prefix = "dev-" if args.dev_read_only else ""
    write_json(ROOT / f"{prefix}evaluation.json", {
        "model": model.spec.__dict__, "documents": len(docs), "vectors": vector_count,
        "aggregates": aggregates, "scenarios": rows,
        "assets_by_document": {doc["id"]: doc["asset_associations"] for doc in docs},
    })
    write_review(docs, rows, aggregates, manifest, vector_count, path=ROOT / f"{prefix}revision-humana.md")
    print(json.dumps({"documents": len(docs), "vectors": vector_count, "aggregates": aggregates}))


if __name__ == "__main__":
    main()
