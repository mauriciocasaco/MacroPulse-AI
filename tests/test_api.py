from datetime import date
from unittest.mock import create_autospec

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend import api
from backend.market_data.models import DateRange
from backend.market_data.repository import AvailableAsset, PostgresPriceRepository


@pytest.fixture
def api_fixture():
    repository = create_autospec(PostgresPriceRepository, instance=True)
    repository.list_available_assets.return_value = (
        AvailableAsset(1, "bitcoin_usd", "Bitcoin", "synthetic", "Synthetic", "BTC-USD", "USD/BTC", "crypto_24x7", None, None, 0),
        AvailableAsset(2, "wti_future", "WTI", "synthetic", "Synthetic", "CL=F", "USD/bbl", "wti", None, None, 0),
    )
    repository.read_daily.return_value = ()
    repository.read_latest_daily.return_value = ()
    repository.compare_daily.return_value = ()
    api.app.dependency_overrides[api.get_repository] = lambda: repository
    try:
        with TestClient(api.app) as client:
            yield client, repository
    finally:
        api.app.dependency_overrides.clear()


def test_liveness_is_available_without_database_configuration(monkeypatch):
    monkeypatch.delenv("MARKET_API_DSN", raising=False)
    with TestClient(api.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/assets").status_code == 503


def test_catalog_and_empty_history_are_distinct_from_unknown_asset(api_fixture):
    client, repository = api_fixture
    catalog = client.get("/assets")
    assert catalog.status_code == 200
    assert [asset["symbol"] for asset in catalog.json()] == ["BTC-USD", "CL=F"]
    response = client.get("/assets/1/history")
    assert response.status_code == 200
    assert response.json() == {"asset": catalog.json()[0], "bars": []}
    repository.read_daily.assert_called_once_with(1, None)
    assert client.get("/assets/3/history").status_code == 404
    assert client.get("/assets/3/latest").status_code == 404


def test_window_latest_and_compare_arguments_are_forwarded(api_fixture):
    client, repository = api_fixture
    window = DateRange(date(2020, 4, 20), date(2020, 4, 21))
    bounds = {"start": "2020-04-20", "end_exclusive": "2020-04-21"}
    assert client.get("/assets/1/history", params=bounds).status_code == 200
    repository.read_daily.assert_called_once_with(1, window)
    assert client.get("/assets/1/latest", params={"limit": 1000}).status_code == 200
    repository.read_latest_daily.assert_called_once_with(1, 1000)
    assert client.get("/assets/1/latest").status_code == 200
    repository.read_latest_daily.assert_called_with(1, 30)
    comparison = client.get("/compare", params={"first": 1, "second": 2, **bounds})
    assert comparison.status_code == 200 and comparison.json()["points"] == []
    repository.compare_daily.assert_called_once_with(1, 2, window)
    assert client.get("/compare", params={"first": 1, "second": 3, **bounds}).status_code == 404


@pytest.mark.parametrize("url", [
    "/assets/0/history",
    "/assets/9223372036854775808/history",
    "/assets/1/history?start=2020-04-20",
    "/assets/1/history?start=2020-04-20&end_exclusive=2020-04-20",
    "/assets/1/history?start=2020-04-21&end_exclusive=2020-04-20",
    "/assets/1/history?start=2020-04-31&end_exclusive=2020-05-01",
    "/assets/1/history?start=2020-04-20T00:00:00&end_exclusive=2020-04-21",
    "/assets/1/history?end=2020-04-21",
    "/assets/1/latest?limit=0",
    "/assets/1/latest?limit=1001",
    "/compare?first=1&second=1&start=2020-04-20&end_exclusive=2020-04-21",
    "/compare?first=1&second=2",
])
def test_invalid_parameters_do_not_query_the_repository(api_fixture, url):
    client, repository = api_fixture
    assert client.get(url).status_code == 422
    repository.list_available_assets.assert_not_called()


def test_database_errors_and_malformed_configuration_are_sanitized(api_fixture, monkeypatch):
    client, repository = api_fixture
    repository.list_available_assets.side_effect = psycopg.OperationalError("fixture-only sensitive detail")
    response = client.get("/assets")
    assert response.status_code == 503
    assert response.json() == {"detail": "Market data unavailable"}
    api.app.dependency_overrides.clear()
    monkeypatch.setenv("MARKET_API_DSN", "unrecognized_option=fixture-only")
    response = client.get("/assets")
    assert response.status_code == 503
    assert response.json() == {"detail": "Market data unavailable"}


def test_repository_config_forces_tls_verification(monkeypatch):
    factory = create_autospec(PostgresPriceRepository)
    monkeypatch.setattr(api, "PostgresPriceRepository", factory)
    monkeypatch.setenv("MARKET_API_DSN", "host=127.0.0.1 sslmode=disable gssencmode=prefer sslrootcert=fixture.crt")
    api.get_repository()
    args, kwargs = factory.call_args
    assert conninfo_to_dict(args[0]) == {
        "host": "127.0.0.1", "sslmode": "verify-full", "gssencmode": "disable", "sslrootcert": "fixture.crt",
        "connect_timeout": "5",
    }
    assert kwargs["connect"] is api._read_only_connection


def test_openapi_documents_parameters_responses_and_routes():
    with TestClient(api.app) as client:
        specification = client.get("/openapi.json").json()
    paths = specification["paths"]
    assert set(paths) == {"/health", "/assets", "/assets/{asset_source_id}/history", "/assets/{asset_source_id}/latest", "/compare"}
    assert set(paths["/compare"]["get"]["responses"]) == {"200", "404", "422", "503"}
    assert {parameter["name"] for parameter in paths["/compare"]["get"]["parameters"]} == {
        "first", "second", "start", "end_exclusive",
    }
