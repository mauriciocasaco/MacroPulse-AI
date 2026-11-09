from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from backend import analysis, api
from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data.migrations import apply_migrations
from backend.market_data.repository import PostgresPriceRepository
from backend.news.embeddings import ModelSpec, embed_record
from backend.news.research_judgments import ReviewedSource


WINDOW = DateRange(date(2020, 4, 20), date(2020, 4, 26))
RETRIEVED_AT = datetime(2020, 4, 26, 12, tzinfo=timezone.utc)


@pytest.fixture
def database(disposable_database):
    with disposable_database("MARKET_API_TEST_DSN", sslmode="verify-full", gssencmode="disable") as dsn:
        apply_migrations(dsn)
        yield dsn


def _seed(repository, asset_id, *, close, adjusted_close, hour, zone, days):
    mapping = ASSET_SOURCES[asset_id]
    rows = tuple(RawRow(int(datetime(2020, 4, day, hour, 30 if hour else 0, tzinfo=timezone.utc).timestamp()), {
        "Open": close, "High": close + 1, "Low": close - 1, "Close": close,
        "Adj Close": adjusted_close, "Volume": 12,
    }) for day in days)
    raw = RawBatch(
        asset_id, mapping, mapping.source_id, mapping.symbol, WINDOW, "1d", RETRIEVED_AT, "fixture",
        rows, {"index_timezone": zone}, "synthetic-1",
    )
    batch = normalize(raw, ASSETS[asset_id], mapping, WINDOW)
    mapping_id = repository.resolve_mapping_id(mapping)
    result = repository.save_batch(repository.start_run(mapping_id, WINDOW), batch)
    assert result.inserted_count == len(days)
    return mapping_id


def test_api_reads_catalog_and_synthetic_prices_over_verified_tls(database, monkeypatch):
    repository = PostgresPriceRepository(database)
    btc = _seed(repository, "bitcoin_usd", close=100, adjusted_close=99.5, hour=0, zone="UTC", days=(20, 25))
    wti = _seed(repository, "wti_future", close=-37.63, adjusted_close=None, hour=13,
                zone="America/New_York", days=(20,))
    monkeypatch.setenv("MARKET_API_DSN", database)
    with TestClient(api.app) as client:
        catalog = client.get("/assets").json()
        assert {entry["symbol"] for entry in catalog} == {"BTC-USD", "CL=F", "GC=F", "^GSPC", "VT", "VWO"}
        assert next(entry for entry in catalog if entry["asset_source_id"] == btc)["bar_count"] == 2
        vt = next(entry["asset_source_id"] for entry in catalog if entry["symbol"] == "VT")
        assert client.get(f"/assets/{vt}/history").json()["bars"] == []

        history = client.get(f"/assets/{btc}/history").json()
        assert history["asset"]["economic_unit"] == "USD/BTC"
        assert [row["bar_date"] for row in history["bars"]] == ["2020-04-20", "2020-04-25"]
        assert [(row["close"], row["adjusted_close"]) for row in history["bars"]] == [("100", "99.5")] * 2
        assert history["bars"][0]["adjusted_close_basis"] == "provider_reported"
        assert history["bars"][0]["retrieved_at"] == "2020-04-26T12:00:00Z"
        assert history["bars"][0]["provider_version"] == "synthetic-1"
        assert history["bars"][0]["source_convention"].startswith("verified:")
        assert history["bars"][0]["availability_time"] is None

        dates = {"start": "2020-04-20", "end_exclusive": "2020-04-25"}
        assert client.get(f"/assets/{btc}/history", params=dates).json()["bars"] == history["bars"][:1]
        assert client.get(f"/assets/{btc}/latest?limit=1").json()["bars"] == history["bars"][1:]
        comparison = client.get("/compare", params={"first": btc, "second": wti, **dates}).json()
        by_id = {point["asset_source_id"]: point for point in comparison["points"]}
        assert set(by_id) == {btc, wti}
        assert (by_id[wti]["economic_unit"], by_id[wti]["close"], by_id[wti]["adjusted_close"]) == (
            "USD/bbl", "-37.63", None,
        )
        assert by_id[wti]["rolling_method"] == "unknown"
        assert by_id[wti]["ohlc_basis"] == "provider_unadjusted"
        assert by_id[btc]["retrieved_at"] == "2020-04-26T12:00:00Z"
        assert client.get("/assets/9223372036854775807/history").status_code == 404


def test_api_connection_blocks_writes_and_requires_verified_tls(database, monkeypatch):
    with api._read_only_connection(database) as connection:
        assert connection.pgconn.ssl_in_use
        assert connection.execute("SHOW transaction_read_only").fetchone() == ("on",)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with connection.transaction():
                connection.execute("CREATE TABLE forbidden_write (value text)")
    with pytest.raises(psycopg.OperationalError, match="requires PostgreSQL TLS"):
        api._read_only_connection(make_conninfo(database, sslmode="disable"))

    monkeypatch.setenv("MARKET_API_DSN", make_conninfo(database, host="untrusted.example"))
    with TestClient(api.app) as client:
        response = client.get("/assets")
        assert response.status_code == 503
        assert response.json() == {"detail": "Market data unavailable"}


def test_analysis_reads_scoped_hybrid_evidence_and_prices_over_tls(database, monkeypatch):
    class FixtureModel:
        spec = ModelSpec("fixture", "v1", 2)

        def embed(self, text, *, query):
            return (1, 0)

    model = FixtureModel()
    reviewed = ReviewedSource("eia", "https://example.test/scoped", date(2020, 4, 20), "direct",
                              "Fixture: constrained storage affects settlement prices.")
    monkeypatch.setattr(analysis, "reviewed_sources", lambda _event, _symbol: (reviewed,))
    repository = PostgresPriceRepository(database)
    _seed(repository, "wti_future", close=-37.63, adjusted_close=None, hour=13,
          zone="America/New_York", days=(20, 21))
    migration_dir = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
    with psycopg.connect(database) as connection:
        for migration in sorted(migration_dir.glob("*_news*.sql")):
            connection.execute(migration.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO news.sources VALUES ('eia', 'Fixture archive', NULL)")
        for path, themes in [("scoped", ["marketpulse:event:covid"]), ("out-of-scope", ["marketpulse:event:ukraine"]),
                             ("unjudged", ["marketpulse:event:covid", "marketpulse:direct:covid:wti_future"])]:
            url = f"https://example.test/{path}"
            article_id = connection.execute("""
                INSERT INTO news.articles (canonical_url, publisher) VALUES (%s, 'Fixture publisher') RETURNING id
            """, (url,)).fetchone()[0]
            record_id = connection.execute("""
                INSERT INTO news.article_records (
                    article_id, source_id, source_url, retrieved_at, reported_published_on, themes,
                    provenance_ref, content_kind, content, storage_permission, rights_basis,
                    embedding_allowed, public_display_allowed
                ) VALUES (%s, 'eia', %s, %s, '2020-04-20', %s, %s, 'excerpt',
                    'fixture-only text', 'excerpt', 'fixture rights', TRUE, TRUE) RETURNING id
            """, (article_id, url, RETRIEVED_AT, themes, f"fixture:{path}")).fetchone()[0]
            connection.execute("""
                INSERT INTO news.record_assets (record_id, asset_id, relationship, confidence, evidence, method_version)
                VALUES (%s, 'wti_future', 'direct', 'high', 'fixture', 'v1')
            """, (record_id,))
            assert embed_record(connection, record_id, model) == "created"
    monkeypatch.setenv("MARKET_API_DSN", database)
    monkeypatch.setenv("MARKET_ANALYSIS_EMBEDDING_CACHE", "synthetic-cache")
    monkeypatch.setattr(api, "_local_embedding", lambda _: model)
    with api._analysis_connection(database) as connection:
        assert connection.execute("SHOW transaction_read_only").fetchone() == ("on",)
        assert connection.execute("SHOW statement_timeout").fetchone() == ("5s",)
        assert connection.execute("SHOW lock_timeout").fetchone() == ("2s",)
    request = {"asset": "CL=F", "event": "covid", "start": "2020-04-20", "end_exclusive": "2020-04-26",
               "question": "How did the pandemic affect oil prices?"}
    with TestClient(api.app) as client:
        response = client.post("/analysis", json=request)
        assert response.status_code == 200
        result = response.json()
        assert result["coverage"] == "sufficient"
        assert [citation["original_url"] for citation in result["citations"]] == ["https://example.test/scoped"]
        assert result["economic_mechanisms"] == [{"citation_id": result["citations"][0]["evidence_id"],
                                                  "summary": reviewed.mechanism}]
        assert result["prices"]["minimum_close"]["close"] == "-37.63"
        assert result["prices"]["close_change_pct"] is None
        assert "fixture-only text" not in response.text
        historical = client.post("/analysis", json={**request, "as_of_exclusive": "2020-04-23"}).json()
        assert historical["coverage"] == "insufficient"
        assert historical["prices"]["observations"] == 0
