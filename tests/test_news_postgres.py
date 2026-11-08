from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest

from backend.market_data.migrations import apply_migrations, inspect_schema


MIGRATION = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "202611080002_news.sql"
PUBLISHED = datetime(2020, 4, 20, 18, tzinfo=timezone.utc)
OBSERVED = datetime(2020, 4, 20, 19, tzinfo=timezone.utc)
RETRIEVED = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def news_dsn(disposable_database):
    with disposable_database("MARKET_DATA_TEST_DSN") as dsn:
        apply_migrations(dsn)
        with psycopg.connect(dsn) as connection:
            before = inspect_schema(connection)
            connection.execute(MIGRATION.read_text(encoding="utf-8"))
            assert inspect_schema(connection) == before
            tables = connection.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'news' AND table_type = 'BASE TABLE'
            """).fetchall()
            assert {name for (name,) in tables} == {"sources", "articles", "article_records", "article_assets"}
        yield dsn


@pytest.fixture
def news_database(news_dsn):
    with psycopg.connect(news_dsn, autocommit=True) as connection:
        connection.execute("SET search_path TO news, pg_catalog")
        connection.execute("TRUNCATE article_assets, article_records, articles, sources RESTART IDENTITY")
        connection.execute("INSERT INTO sources VALUES ('archive', 'Synthetic Archive', 'https://example.test/terms')")
        yield connection


def _article(connection, path="wti", published_at=PUBLISHED, published_on=None):
    return connection.execute("""
        INSERT INTO articles (canonical_url, publisher, title, published_at, published_on)
        VALUES (%s, 'Synthetic Publisher', 'Synthetic market report', %s, %s) RETURNING id
    """, (f"https://example.test/{path}", published_at, published_on)).fetchone()[0]


def _record(connection, article, *, source_id="archive", external_id="observed-1", path="wti?ref=archive",
            observed=OBSERVED, published=PUBLISHED, published_on=None, provenance="fixture:archive/20200420#1",
            content_kind="none", content=None, permission="metadata_only", rights=None,
            public_display=False, embedding=False):
    return connection.execute("""
        INSERT INTO article_records (
            article_id, source_id, external_id, source_url, retrieved_at, provider_observed_at,
            reported_published_at, reported_published_on, provenance_ref, content_kind, content,
            storage_permission, rights_basis, public_display_allowed, embedding_allowed
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (article, source_id, external_id, f"https://example.test/{path}", RETRIEVED, observed, published,
          published_on, provenance, content_kind, content, permission, rights, public_display, embedding)).fetchone()[0]


def _link(connection, article, asset):
    connection.execute("INSERT INTO article_assets (article_id, asset_id, evidence) VALUES (%s, %s, 'fixture topic')",
                       (article, asset))


def test_news_round_trip_preserves_provenance_precision_and_defaults(news_database):
    connection = news_database
    article = _article(connection)
    connection.execute("""
        INSERT INTO article_records (article_id, source_id, external_id, source_url, retrieved_at,
                                     provider_observed_at, reported_published_at, provenance_ref)
        VALUES (%s, 'archive', 'observed-1', 'https://example.test/wti?ref=archive', %s, %s, %s, 'fixture:archive/20200420#1')
    """, (article, RETRIEVED, OBSERVED, PUBLISHED))
    stored = connection.execute("""
        SELECT a.canonical_url, a.publisher, a.published_at, r.provider_observed_at, r.retrieved_at,
               r.reported_published_at, r.external_id, r.source_url, r.provenance_ref, s.terms_url,
               r.content_kind, r.content, r.storage_permission, r.public_display_allowed, r.embedding_allowed
        FROM articles a JOIN article_records r ON r.article_id = a.id JOIN sources s ON s.id = r.source_id
        WHERE a.id = %s
    """, (article,)).fetchone()
    assert stored == (
        "https://example.test/wti", "Synthetic Publisher", PUBLISHED, OBSERVED, RETRIEVED, PUBLISHED,
        "observed-1", "https://example.test/wti?ref=archive", "fixture:archive/20200420#1", "https://example.test/terms",
        "none", None, "metadata_only", False, False,
    )
    date_only = _article(connection, "daily-report", published_at=None, published_on=date(2020, 4, 20))
    record = _record(connection, date_only, external_id="daily-report", published=None, published_on=date(2020, 4, 20))
    assert connection.execute("SELECT reported_published_at, reported_published_on FROM article_records WHERE id = %s",
                              (record,)).fetchone() == (None, date(2020, 4, 20))


def test_deduplication_keeps_distinct_provider_observations_and_assets(news_database):
    connection = news_database
    article = _article(connection)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _article(connection)
    _record(connection, article)
    second = _record(connection, article, external_id="observed-2")
    connection.execute("INSERT INTO sources VALUES ('second_archive', 'Synthetic Archive Two', NULL)")
    third = _record(connection, article, source_id="second_archive")
    assert second != third
    with pytest.raises(psycopg.errors.UniqueViolation):
        _record(connection, article, path="different-url-with-same-id")
    _record(connection, article, external_id=None)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _record(connection, article, external_id=None)
    for asset in ("wti_future", "sp500_index"):
        _link(connection, article, asset)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _link(connection, article, "wti_future")
    assert connection.execute("SELECT count(*) FROM article_assets WHERE article_id = %s", (article,)).fetchone() == (2,)


def test_news_foreign_keys_reference_articles_sources_and_market_assets(news_database):
    connection = news_database
    article = _article(connection)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _record(connection, -1)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _record(connection, article, source_id="unregistered")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _link(connection, article, "unregistered")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _link(connection, -1, "wti_future")


@pytest.mark.parametrize("changes", [
    {"observed": datetime(2030, 1, 1, tzinfo=timezone.utc)},
    {"published": datetime(2030, 1, 1, tzinfo=timezone.utc)},
    {"published_on": date(2020, 4, 20)},
    {"provenance": "  "},
    {"content": "unclassified text"},
    {"content_kind": "excerpt", "content": "unlicensed excerpt"},
    {"content_kind": "full", "content": "unauthorized full body", "permission": "excerpt", "rights": "fixture license"},
    {"content_kind": "full", "content": "unauthorized full body", "permission": "full"},
    {"content_kind": "excerpt", "permission": "excerpt", "rights": "fixture license"},
    {"public_display": True},
    {"embedding": True},
])
def test_record_checks_reject_inconsistent_dates_and_rights(news_database, changes):
    connection = news_database
    article = _article(connection)
    with pytest.raises(psycopg.errors.CheckViolation):
        _record(connection, article, **changes)


def test_news_period_query_keeps_partial_content_and_excludes_undated_articles(news_database):
    connection = news_database
    article = _article(connection)
    unknown = _article(connection, "undated", published_at=None)
    after = _article(connection, "after", published_at=datetime(2020, 4, 21, tzinfo=timezone.utc))
    other_asset = _article(connection, "other-asset")
    _record(connection, article, content_kind="excerpt", content="Synthetic excerpt", permission="excerpt",
            rights="fixture terms section 2")
    _record(connection, unknown, external_id="undated", path="undated", published=None)
    for news_id in (article, unknown, after):
        _link(connection, news_id, "wti_future")
    _link(connection, other_asset, "bitcoin_usd")
    rows = connection.execute("""
        SELECT a.id, a.published_at FROM article_assets aa JOIN articles a ON a.id = aa.article_id
        WHERE aa.asset_id = %s AND a.published_at >= %s AND a.published_at < %s
        ORDER BY a.published_at, a.id
    """, ("wti_future", datetime(2020, 4, 20, tzinfo=timezone.utc), datetime(2020, 4, 21, tzinfo=timezone.utc))).fetchall()
    assert rows == [(article, PUBLISHED)]
    assert connection.execute("SELECT content_kind, content, embedding_allowed FROM article_records WHERE article_id = %s",
                              (article,)).fetchone() == ("excerpt", "Synthetic excerpt", False)
