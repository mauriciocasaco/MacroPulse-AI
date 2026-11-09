from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from zipfile import ZipFile

import psycopg
import pytest

from backend.market_data.migrations import apply_migrations
from backend.news.assets import associate
from backend.news.embeddings import ModelSpec, embed_record, metadata_text, vector_literal
from backend.news.evaluation import Judgment, evaluate
from backend.news.gdelt import canonicalize_url, read_batch
from backend.news.ingest import local_dsn, main, write_batch
from backend.news.retrieval import Search, search


MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
STAMP = "20200420210000"
OBSERVED = datetime(2020, 4, 20, 21, tzinfo=UTC)
RETRIEVED = datetime(2026, 10, 1, tzinfo=UTC)


def gkg_row(sequence, url, *, publication="", themes="", organizations="", provider_type="1"):
    fields = [""] * 27
    fields[:5] = [f"{STAMP}-{sequence}", STAMP, provider_type, "News.Example.Test", url]
    fields[7] = themes
    fields[11] = "Private Person"
    fields[13] = organizations
    if publication:
        fields[26] = f"<PAGE_PRECISEPUBTIMESTAMP>{publication}</PAGE_PRECISEPUBTIMESTAMP>"
    return "\t".join(fields)


def archive(tmp_path, *rows):
    path = tmp_path / f"{STAMP}.gkg.csv.zip"
    with ZipFile(path, "w") as output:
        output.writestr(path.name.removesuffix(".zip"), "\n".join(rows) + "\n")
    return path


def test_parse_real_schema_shape_and_dry_run(tmp_path, monkeypatch, capsys):
    first = gkg_row(
        1, "https://News.Example.Test/wti-crude?item=1&utm_medium=feed#fragment",
        publication="2020-04-20T16:30:00-04:00", themes="ECON_OILPRICE;econ_oilprice;",
        organizations="EIA;EIA",
    )
    second = gkg_row("T2", "https://news.example.test/gold-price", publication="2020-04-20")
    unsupported = gkg_row(3, "https://news.example.test/test", provider_type="2")
    malformed = f"{STAMP}-4\tgarbage"
    path = archive(tmp_path, first, first, second, unsupported, malformed)
    ids = [f"{STAMP}-{number}" for number in [1, "T2", 3, 4, 5]]
    batch = read_batch(path, ids, RETRIEVED)
    assert batch.processed == 5 and batch.duplicates == 1
    assert batch.rejected == {"unsupported source type": 1, "malformed row": 1, "selected record missing": 1}
    one, two = batch.records
    assert one.source_url == "https://News.Example.Test/wti-crude?item=1&utm_medium=feed"
    assert one.canonical_url == "https://news.example.test/wti-crude?item=1"
    assert one.published_at == OBSERVED - timedelta(minutes=30)
    assert one.observed_at == OBSERVED and one.retrieved_at == RETRIEVED
    assert one.publication_raw == "2020-04-20T16:30:00-04:00"
    assert one.themes == ("ECON_OILPRICE",) and one.entities == ("EIA",)
    assert one.provenance_ref.endswith(f"{STAMP}.gkg.csv.zip#{STAMP}-1")
    assert two.published_at is None and two.published_on == date(2020, 4, 20)
    assert associate(one)[0].relationship == "direct"
    assert associate(two)[0].relationship == "thematic"
    monkeypatch.setattr(psycopg, "connect", lambda *_: pytest.fail("dry-run opened PostgreSQL"))
    monkeypatch.setattr("sys.argv", ["ingest", str(path), "--dry-run", "--record-id", ids[0],
                                    "--retrieved-at", RETRIEVED.isoformat()])
    main()
    assert json.loads(capsys.readouterr().out)["normalized"] == 1


@pytest.mark.parametrize("value", [
    "file:///private/path", "https://user:pass@example.test/path", "https://example.test/a?%74oken=restricted",
    "https://example.test/a?x-amz-signature=restricted", "https://example.test:bad/a",
    "https://exam\tple.test/a", "https://example.test\\bad", "https://example.test/path?ref=x;token=restricted",
])
def test_unsafe_urls_are_rejected(value):
    with pytest.raises(ValueError):
        canonicalize_url(value)


def test_url_and_publication_identity_are_conservative(tmp_path):
    assert canonicalize_url("http://News.Example.Test:80/Path?z=a+b&z=a%20b&x=%2F#x") == (
        "http://news.example.test:80/Path?z=a+b&z=a%20b&x=%2F"
    )
    path = archive(tmp_path, gkg_row(1, "https://example.test/wti", publication="20200421010000"))
    row = read_batch(path, [f"{STAMP}-1"], RETRIEVED).records[0]
    assert row.published_at is None and row.publication_raw == "20200421010000"
    with pytest.raises(ValueError, match="timezone"):
        read_batch(path, [f"{STAMP}-1"], RETRIEVED.replace(tzinfo=None))


def test_bare_etf_symbols_and_incidental_btc_theme_are_not_direct(tmp_path):
    path = archive(tmp_path, gkg_row(
        1, "https://example.test/vt-vwo-fed-rate-hike", themes="ECON_BITCOIN;ENV_OIL",
    ))
    links = associate(read_batch(path, [f"{STAMP}-1"], RETRIEVED).records[0])
    assert {(link.asset_id, link.relationship) for link in links} == {
        ("world_equity_etf", "macro"), ("emerging_markets_etf", "macro"),
    }


@pytest.mark.parametrize("dsn", [
    "host=remote.example.test port=5432 dbname=postgres",
    "host=localhost port=5432 dbname=postgres",
    "host=127.0.0.1 hostaddr=192.0.2.1 port=5432 dbname=postgres",
    "host=127.0.0.1 port=5432 dbname=postgres service=remote",
    "host=127.0.0.1 dbname=postgres",
])
def test_dsn_rejects_non_local_or_implicit_targets(dsn):
    with pytest.raises(ValueError):
        local_dsn(dsn)


@pytest.mark.parametrize("values", [(1,), (0, 0), (1, float("nan")), (1, float("inf")),
                                      ("1", 0), (True, False), (4e39, 0), (1e-80, 0)])
def test_invalid_vectors(values):
    with pytest.raises(ValueError):
        vector_literal(values, ModelSpec("synthetic", "v1", 2))


def test_large_float32_vector_is_normalized_before_storage():
    values = json.loads(vector_literal((3e38, -3e38), ModelSpec("synthetic", "v1", 2)))
    assert values == pytest.approx([2 ** -0.5, -(2 ** -0.5)])


def test_metadata_representation_and_metrics():
    assert metadata_text("example.test", ["ECON_OILPRICE"], ["EIA"]) == (
        "Provider metadata\nPublisher: example.test\nThemes: ECON_OILPRICE\nOrganizations: EIA"
    )
    cases = [
        Judgment(frozenset({"a", "b"}), ("x", "a", "b")),
        Judgment(frozenset({"c"}), ("x",)),
        Judgment(frozenset(), ()),
    ]
    assert evaluate(cases, k=2) == {
        "k": 2, "positive_cases": 2, "no_evidence_cases": 1, "false_positive_cases": 0,
        "hit_at_k": 0.5, "recall_at_k": 0.25, "precision_at_k": 0.25, "mrr_at_k": 0.25,
    }


@pytest.fixture(scope="module")
def pipeline_dsn(disposable_database):
    with disposable_database("MARKETPULSE_NEWS_LOCAL_DSN") as dsn:
        apply_migrations(dsn)
        with psycopg.connect(dsn) as connection:
            for migration in sorted(MIGRATIONS.glob("*_news*.sql")):
                connection.execute(migration.read_text(encoding="utf-8"))
        yield dsn


@pytest.fixture
def db(pipeline_dsn):
    with psycopg.connect(pipeline_dsn, autocommit=True) as connection:
        connection.execute("""
            TRUNCATE news.record_embeddings, news.embedding_models, news.record_assets,
                news.article_assets, news.ingestion_batches, news.article_records, news.articles, news.sources
            RESTART IDENTITY
        """)
        yield connection


def test_migration_rejects_existing_vector_in_unexpected_schema(disposable_database):
    with disposable_database("MARKETPULSE_NEWS_LOCAL_DSN") as dsn:
        apply_migrations(dsn)
        paths = sorted(MIGRATIONS.glob("*_news*.sql"))
        with psycopg.connect(dsn, autocommit=True) as connection:
            for migration in paths[:-1]:
                connection.execute(migration.read_text(encoding="utf-8"))
            connection.execute("CREATE EXTENSION vector WITH SCHEMA public")
            with pytest.raises(psycopg.errors.RaiseException, match="pgvector must be installed in the extensions schema"):
                connection.execute(paths[-1].read_text(encoding="utf-8"))
            assert connection.execute("""
                SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
                WHERE e.extname = 'vector'
            """).fetchone() == ("public",)


def test_checkpoint_atomic_retry_and_conflict(db, tmp_path):
    path = archive(tmp_path, gkg_row(1, "https://example.test/wti-crude?utm_campaign=test"),
                   gkg_row(2, "https://example.test/wti-crude"))
    ids = [f"{STAMP}-1", f"{STAMP}-2"]
    batch = read_batch(path, ids, RETRIEVED)
    with pytest.raises(RuntimeError):
        with db.transaction():
            write_batch(db, batch)
            raise RuntimeError("synthetic interruption")
    assert db.execute("SELECT count(*) FROM news.article_records").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM news.ingestion_batches").fetchone() == (0,)
    result = write_batch(db, batch)
    assert result["new_articles"] == 1 and result["new_provider_records"] == 2
    assert result["existing_canonical_urls"] == 1
    replay = read_batch(path, ids, RETRIEVED + timedelta(days=1))
    assert write_batch(db, replay)["checkpoint_replay"]
    assert db.execute("""
        SELECT count(*), min(retrieved_at), max(retrieved_at), count(content) FROM news.article_records
    """).fetchone() == (2, RETRIEVED, RETRIEVED, 0)
    assert db.execute("SELECT count(*) FROM news.record_assets").fetchone() == (2,)
    assert db.execute("SELECT count(*) FROM news.article_assets").fetchone() == (1,)
    result = search(db, Search("WTI crude", ("CL=F",), OBSERVED - timedelta(days=1), OBSERVED + timedelta(days=1)))
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["publication_precision"] == "unknown"
    assert result["evidence"][0]["event_precision"] == "unknown"
    assert result["evidence"][0]["evidence_kind"] == "provider_metadata"

    changed = archive(tmp_path, gkg_row(1, "https://example.test/not-the-same"))
    with pytest.raises(ValueError, match="checkpoint"):
        write_batch(db, replace(batch, archive_sha256="a" * 64))
    conflict = write_batch(db, read_batch(changed, [ids[0]], RETRIEVED))
    assert conflict["provider_conflicts"] == 1
    assert db.execute("SELECT count(*) FROM news.articles").fetchone() == (1,)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("""INSERT INTO news.record_assets VALUES
                    (1, 'unknown', 'direct', 'high', 'fixture', 'v1', clock_timestamp())""")


class SyntheticModel:
    spec = ModelSpec("synthetic", "v1", 2)

    def embed(self, text, *, query):
        if "break" in text:
            raise RuntimeError("synthetic provider failure")
        if "negative" in text:
            return (-1, 0)
        return (0, 1) if "other" in text else (1, 0)


def add_fixture(db, path, asset, *, text=None, display=True, observed=OBSERVED,
                published=OBSERVED - timedelta(hours=1), retrieved=RETRIEVED,
                linked=RETRIEVED, synthetic=True, source="fixture"):
    db.execute("INSERT INTO news.sources VALUES (%s, 'Synthetic fixture', NULL) ON CONFLICT DO NOTHING", (source,))
    url = f"https://example.test/{path}"
    article_id = db.execute("""
        INSERT INTO news.articles (canonical_url, publisher) VALUES (%s, 'Synthetic publisher') RETURNING id
    """, (url,)).fetchone()[0]
    record_id = db.execute("""
        INSERT INTO news.article_records (
            article_id, source_id, external_id, source_url, retrieved_at, provider_observed_at,
            reported_published_at, provenance_ref, content_kind, content, storage_permission,
            rights_basis, public_display_allowed, embedding_allowed, is_synthetic
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (article_id, source, path, url, retrieved, observed, published, f"fixture:{path}",
          "excerpt" if text else "none", text, "excerpt" if text else "metadata_only",
          "synthetic" if text else None, bool(text and display), bool(text), synthetic)).fetchone()[0]
    db.execute("""
        INSERT INTO news.record_assets VALUES (%s, %s, 'direct', 'high', 'synthetic', 'fixture-v1', %s)
    """, (record_id, asset, linked))
    return record_id


def test_pgvector_versioned_write_rank_and_failure_recovery(db):
    model = SyntheticModel()
    article = add_fixture(db, "oil-storage", "wti_future", text="Synthetic storage scenario.")
    other = add_fixture(db, "oil-supply", "wti_future", text="Synthetic other scenario.", display=False)
    failing = add_fixture(db, "broken", "wti_future", text="break")
    denied = add_fixture(db, "unlicensed", "wti_future")
    assert embed_record(db, article, model) == "created"
    assert embed_record(db, article, model) == "unchanged"
    assert embed_record(db, other, model) == "created"
    with pytest.raises(RuntimeError):
        embed_record(db, failing, model)
    with pytest.raises(ValueError, match="approved"):
        embed_record(db, denied, model)
    assert db.execute("SELECT count(*) FROM news.record_embeddings").fetchone() == (2,)
    request = Search("oil storage", ("CL=F",), OBSERVED - timedelta(days=1), OBSERVED + timedelta(days=1),
                     sources=("fixture",), include_synthetic=True)
    evidence = search(db, request, model)["evidence"]
    assert [item["external_id"] for item in evidence] == ["oil-storage", "oil-supply"]
    assert [item["cosine_similarity"] for item in evidence] == [1.0, None]
    assert evidence[1]["passage"] is None and evidence[1]["evidence_kind"] == "provider_metadata"
    assert evidence[0]["semantic_model"] == {"id": "synthetic", "revision": "v1"}
    negative = search(db, replace(request, question="negative astronomy"), model)
    assert negative["status"] == "insufficient_evidence"
    assert negative["min_cosine_similarity"] == 0.75 and negative["evidence"] == []

    db.execute("UPDATE news.article_records SET content = 'Synthetic changed scenario.' WHERE id = %s", (article,))
    assert {item["external_id"]: item["cosine_similarity"] for item in search(db, request, model)["evidence"]} == {
        "oil-supply": None, "oil-storage": None,
    }
    assert embed_record(db, article, model) == "updated"
    db.execute("UPDATE news.article_records SET content = 'recovered' WHERE id = %s", (failing,))
    assert embed_record(db, failing, model) == "created"
    model.spec = ModelSpec("synthetic", "v1", 3)
    with pytest.raises(ValueError, match="incompatible"):
        embed_record(db, article, model)
    model.spec = ModelSpec("synthetic", "v2", 2)
    assert embed_record(db, article, model) == "created"
    assert db.execute("SELECT count(*) FROM news.record_embeddings WHERE record_id = %s", (article,)).fetchone() == (2,)
    db.execute("UPDATE news.article_records SET embedding_allowed = false WHERE id = %s", (article,))
    assert all(item["cosine_similarity"] is None for item in search(db, request, model)["evidence"])
    assert search(db, replace(request, include_synthetic=False), model)["status"] == "insufficient_evidence"


def test_pgvector_large_inputs_and_configured_semantic_threshold(db):
    class ScaledModel(SyntheticModel):
        def embed(self, text, *, query):
            return (1.8e38, 2.4e38) if query else (3e38, 0)

    model = ScaledModel()
    record = add_fixture(db, "oil-storage", "wti_future", text="Synthetic storage scenario.")
    assert embed_record(db, record, model) == "created"
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE news.record_embeddings SET embedding = '[3e38,0]'::extensions.vector")
    request = Search("almacenamiento", ("CL=F",), OBSERVED - timedelta(days=1), OBSERVED + timedelta(days=1),
                     sources=("fixture",), include_synthetic=True)
    assert search(db, request, model)["status"] == "insufficient_evidence"
    result = search(db, replace(request, min_cosine_similarity=0.55), model)
    assert result["evidence"][0]["cosine_similarity"] == pytest.approx(0.6)
    with pytest.raises(ValueError, match="minimum cosine"):
        search(db, replace(request, min_cosine_similarity=float("nan")), model)


def test_point_in_time_provenance_and_date_precision(db):
    before = OBSERVED - timedelta(days=1)
    cutoff = OBSERVED + timedelta(minutes=5)
    later = OBSERVED + timedelta(days=1)
    add_fixture(db, "oil-on-time", "wti_future", retrieved=OBSERVED, linked=OBSERVED, synthetic=False)
    add_fixture(db, "oil-late-ingest", "wti_future", synthetic=False)
    add_fixture(db, "oil-late-link", "wti_future", retrieved=OBSERVED, synthetic=False)
    add_fixture(db, "oil-unknown-publication", "wti_future", published=None, retrieved=OBSERVED,
                linked=OBSERVED, synthetic=False)
    add_fixture(db, "oil-future-published", "wti_future", published=later, synthetic=False)
    add_fixture(db, "oil-future-observed", "wti_future", observed=later, synthetic=False)
    add_fixture(db, "oil-other-source", "wti_future", retrieved=OBSERVED, linked=OBSERVED,
                synthetic=False, source="other")
    request = Search("oil", ("CL=F",), before, later + timedelta(days=1), ("fixture",), as_of=cutoff)
    system = search(db, request)["evidence"]
    assert [item["external_id"] for item in system] == ["oil-on-time"]
    assert system[0]["publication_precision"] == "timestamp_reported"
    assert system[0]["assets"][0]["linked_at"] and system[0]["source_id"] == "fixture"
    archive_evidence = search(db, replace(request, as_of_mode="archive"))["evidence"]
    assert {item["external_id"] for item in archive_evidence} == {"oil-on-time", "oil-late-ingest", "oil-late-link"}
    assert search(db, replace(request, window_basis="event"))["status"] == "insufficient_evidence"
    db.execute("UPDATE news.article_records SET themes = ARRAY['later'] WHERE external_id = 'oil-on-time'")
    assert search(db, request)["status"] == "insufficient_evidence"


def test_synthetic_retrieval_evaluation(db):
    start = OBSERVED - timedelta(days=1)
    end = OBSERVED + timedelta(days=1)
    cases = [
        ("CL=F", "wti_future", "wti oil April 2020", "wti-april-2020"),
        ("GC=F", "gold_future", "gold during uncertainty", "gold-uncertainty"),
        ("^GSPC", "sp500_index", "stocks macroeconomic policy", "stocks-macroeconomic-policy"),
        ("BTC-USD", "bitcoin_usd", "bitcoin exchange", "bitcoin-exchange"),
        ("VT", "world_equity_etf", "global equities", "global-equities"),
        ("VWO", "emerging_markets_etf", "emerging markets", "emerging-markets"),
    ]
    judgments = []
    for ticker, asset, question, path in cases:
        evidence = add_fixture(db, path, asset)
        add_fixture(db, f"{path.split('-')[0]}-distractor", asset)
        result = search(db, Search(question, (ticker,), start, end, limit=1, include_synthetic=True))
        ranked = tuple(item["evidence_id"] for item in result["evidence"])
        judgments.append(Judgment(frozenset({f"news-record:{evidence}"}), ranked))
        assert all(item["source_id"] == "fixture" and item["provenance_ref"].startswith("fixture:")
                   and item["is_synthetic"] and start <= datetime.fromisoformat(item["provider_observed_at"]) < end
                   for item in result["evidence"])
    for negative in (
        Search("unrelated astronomy", ("CL=F",), start, end, include_synthetic=True),
        Search("oil", ("CL=F",), end, end + timedelta(days=1), include_synthetic=True),
    ):
        result = search(db, negative)
        judgments.append(Judgment(frozenset(), tuple(item["evidence_id"] for item in result["evidence"])))
        assert result["status"] == "insufficient_evidence"
    assert evaluate(judgments, k=1) == {
        "k": 1, "positive_cases": 6, "no_evidence_cases": 2, "false_positive_cases": 0,
        "hit_at_k": 1.0, "recall_at_k": 1.0, "precision_at_k": 1.0, "mrr_at_k": 1.0,
    }
