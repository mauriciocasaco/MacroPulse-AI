from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from backend import api
from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data.migrations import apply_migrations
from backend.market_data.repository import PostgresPriceRepository


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
