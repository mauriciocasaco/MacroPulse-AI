from copy import deepcopy
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.market_data import ASSET_SOURCES, DateRange, YahooAdapter
from backend.market_data.yahoo_adapter import (
    YahooAdapterError,
    YahooDataStructureError,
    YahooEmptyResultError,
    YahooNetworkError,
    YahooRateLimitError,
    YahooTimeoutError,
    YahooUnexpectedResponseError,
    YFINANCE_VERSION,
    _CapturedYfData,
    _YFinanceChartClient,
    RawChartResponse,
)


WINDOW = DateRange(date(2026, 10, 1), date(2026, 11, 1))
SAMPLE_TIMESTAMP = int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp())


def chart_payload(
    symbol="VT",
    *,
    timestamps=None,
    quote=None,
    adjclose=None,
    timezone_name="America/New_York",
):
    indicators = {"quote": [quote or {"open": [10], "high": [12], "low": [9], "close": [11], "volume": [0]}]}
    if adjclose is not None:
        indicators["adjclose"] = [{"adjclose": adjclose}]
    return {
        "chart": {
            "result": [{
                "meta": {
                    "symbol": symbol,
                    "exchangeTimezoneName": timezone_name,
                    "gmtoffset": -14400,
                    "instrumentType": "ETF",
                    "currency": "USD",
                    "priceHint": 2,
                    "exchangeName": "NYQ",
                    "validRanges": ["1d"],
                },
                "timestamp": timestamps or [SAMPLE_TIMESTAMP],
                "indicators": indicators,
            }],
            "error": None,
        }
    }


class FakeChartClient:
    def __init__(self, payload=None, error=None, metadata_payload=None):
        self.payload = payload or chart_payload()
        self.error = error
        self.metadata_payload = metadata_payload or self.payload
        self.calls = []

    def fetch_chart(self, mapping, window):
        self.calls.append((mapping, window))
        if self.error:
            raise self.error
        results = self.payload.get("chart", {}).get("result")
        timezone_name = "UTC"
        if results and isinstance(results, list):
            timezone_name = results[0].get("meta", {}).get("exchangeTimezoneName", "UTC")
        zone = ZoneInfo(timezone_name)
        period1 = int(datetime.combine(window.start, time.min, zone).timestamp())
        period2 = int(datetime.combine(window.end_exclusive, time.min, zone).timestamp())
        return RawChartResponse(self.payload, self.metadata_payload, datetime.now(timezone.utc), period1, period2)


class FakeResponse:
    text = "ok"

    def __init__(self, payload, status_code=200, json_error=None):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return deepcopy(self.payload)


class FakeYfData:
    def __init__(self, session):
        self.responses = []
        self.chart_payload = None
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        response = self.responses.pop(0)
        if not isinstance(response, FakeResponse):
            response = FakeResponse(response)
        self.chart_payload = response.payload
        return response


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_catalog_contains_six_distinct_instrument_mappings():
    assert {key: value.symbol for key, value in ASSET_SOURCES.items()} == {
        "gold_future": "GC=F",
        "wti_future": "CL=F",
        "sp500_index": "^GSPC",
        "world_equity_etf": "VT",
        "emerging_markets_etf": "VWO",
        "bitcoin_usd": "BTC-USD",
    }
    assert ASSET_SOURCES["bitcoin_usd"].instrument_type == "cryptocurrency_spot"
    assert ASSET_SOURCES["gold_future"].instrument_type == "future"
    assert ASSET_SOURCES["gold_future"].rolling_method == "unknown"


@pytest.mark.parametrize("asset_id", list(ASSET_SOURCES))
def test_all_catalog_symbols_are_requested(asset_id):
    client = FakeChartClient(chart_payload(ASSET_SOURCES[asset_id].symbol))
    batch = YahooAdapter(client).fetch_daily(ASSET_SOURCES[asset_id], WINDOW)
    assert client.calls == [(ASSET_SOURCES[asset_id], WINDOW)]
    assert batch.ticker == ASSET_SOURCES[asset_id].symbol


def test_adapter_uses_raw_timestamp_and_raw_ohlcv_values():
    payload = chart_payload(
        "CL=F",
        quote={"open": [-5], "high": [2], "low": [-8], "close": [-3], "volume": [0]},
        adjclose=[-3],
    )
    batch = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["wti_future"], WINDOW)
    assert batch.rows[0].original_label == SAMPLE_TIMESTAMP
    assert dict(batch.rows[0].fields) == {
        "Open": -5, "High": 2, "Low": -8, "Close": -3, "Volume": 0, "Adj Close": -3,
    }
    assert batch.temporal_metadata["index_timezone"] == "America/New_York"
    assert batch.temporal_metadata["provider_session_label_convention"] == "unverified"
    assert batch.provider_version == YFINANCE_VERSION
    assert batch.retrieved_at.tzinfo == timezone.utc


def test_absent_adjusted_close_is_not_synthesized_and_volume_null_is_not_zero_filled():
    payload = chart_payload(
        "VT",
        quote={"open": [10], "high": [12], "low": [9], "close": [11], "volume": [None]},
    )
    batch = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)
    assert "Adj Close" not in batch.rows[0].fields
    assert batch.rows[0].fields["Volume"] is None


def test_yfinance_boundary_fixture_confirms_why_raw_chart_capture_is_required():
    from yfinance.scrapers.history import PriceHistory

    payload = chart_payload(
        "VT",
        quote={"open": [10], "high": [12], "low": [9], "close": [11], "volume": [None]},
    )

    class FakeHistoryTransport:
        def get(self, **kwargs):
            return FakeResponse(payload)

        def cache_get(self, **kwargs):
            return FakeResponse(payload)

    processed = PriceHistory(FakeHistoryTransport(), "VT", "America/New_York").history(
        start="2026-10-01", end="2026-11-01", interval="1d",
        auto_adjust=False, back_adjust=False, repair=False, keepna=True,
        rounding=False, timeout=20, actions=False, prepost=False,
    )
    raw = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)
    assert processed.iloc[0]["Adj Close"] == 11
    assert processed.iloc[0]["Volume"] == 0
    assert "Adj Close" not in raw.rows[0].fields
    assert raw.rows[0].fields["Volume"] is None


def test_explicit_yfinance_history_options_and_start_end_bounds():
    metadata = chart_payload("CL=F")
    history_payload = chart_payload("CL=F")
    captured_data = FakeYfData(None)
    captured_data.responses = [metadata, history_payload]
    session = FakeSession()
    client = _YFinanceChartClient(
        session_factory=lambda: session,
        data_factory=lambda session: captured_data,
        base_url="https://query2.finance.yahoo.com",
    )
    raw = client.fetch_chart(ASSET_SOURCES["wti_future"], WINDOW)
    assert raw.payload == history_payload
    assert raw.received_at.tzinfo == timezone.utc
    assert captured_data.calls[0][1] == {
        "range": "1d", "interval": "1d", "includePrePost": False,
        "events": "div,splits,capitalGains",
    }
    assert len(captured_data.calls) == 2
    assert captured_data.calls[1][1] == {
        "period1": 1790827200, "period2": 1793505600, "interval": "1d",
        "includePrePost": False, "events": "div,splits,capitalGains",
    }
    assert captured_data.calls[1][2] == 20
    assert session.closed


def test_metadata_and_history_ticker_symbols_must_both_match_exactly():
    correct = chart_payload("^GSPC")
    wrong = chart_payload("WRONG")
    with pytest.raises(YahooUnexpectedResponseError):
        YahooAdapter(FakeChartClient(correct, metadata_payload=wrong)).fetch_daily(
            ASSET_SOURCES["sp500_index"], WINDOW
        )
    with pytest.raises(YahooUnexpectedResponseError):
        YahooAdapter(FakeChartClient(wrong, metadata_payload=correct)).fetch_daily(
            ASSET_SOURCES["sp500_index"], WINDOW
        )


def test_http_status_and_invalid_json_are_classified_without_payload():
    with pytest.raises(YahooRateLimitError):
        _YFinanceChartClient._decode_response(FakeResponse({}, status_code=429))
    with pytest.raises(YahooUnexpectedResponseError):
        _YFinanceChartClient._decode_response(FakeResponse({}, status_code=503))
    with pytest.raises(YahooDataStructureError) as error:
        _YFinanceChartClient._decode_response(FakeResponse({}, json_error=ValueError("private response")))
    assert "private response" not in str(error.value)


def test_private_yfinance_data_instance_does_not_register_or_replace_singleton():
    from curl_cffi import requests as curl_requests
    from yfinance.data import YfData

    before = YfData._instances.get(YfData)
    captured = _CapturedYfData(curl_requests.Session(impersonate="chrome"))
    assert captured._instance is not before
    assert YfData._instances.get(YfData) is before
    assert captured._instance._save_cookie_curlCffi() is False
    assert captured._instance._load_cookie_curlCffi() is False


def test_bitcoin_weekend_epoch_is_kept_without_date_synthesis():
    saturday = int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp())
    payload = chart_payload("BTC-USD", timestamps=[saturday])
    batch = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["bitcoin_usd"], WINDOW)
    assert batch.rows[0].original_label == saturday


def test_exact_end_epoch_is_warned_and_preserved_alongside_start_boundary():
    zone = ZoneInfo("UTC")
    period1 = int(datetime.combine(WINDOW.start, time.min, zone).timestamp())
    period2 = int(datetime.combine(WINDOW.end_exclusive, time.min, zone).timestamp())
    payload = chart_payload(
        "BTC-USD",
        timestamps=[period1 - 1, period1, period2],
        quote={"open": [1, 1, 1], "high": [2, 2, 2], "low": [0, 0, 0], "close": [1, 1, 1], "volume": [1, 1, 1]},
        timezone_name="UTC",
    )
    batch = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["bitcoin_usd"], WINDOW)
    assert [row.original_label for row in batch.rows] == [period1 - 1, period1, period2]
    assert batch.temporal_metadata["out_of_range_timestamps"] == (period1 - 1, period2)
    assert any(str(period2) in warning and "Raw rows are retained" in warning for warning in batch.warnings)


def test_unrecognized_provider_field_is_preserved():
    payload = chart_payload("VT", quote={"close": [11], "providerMetric": [7]})
    batch = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)
    assert batch.rows[0].fields["providerMetric"] == 7


@pytest.mark.parametrize("indicator", [None, {}, [], [None], [{}], [{"adjclose": None}], [{"adjclose": []}]])
def test_present_malformed_adjusted_close_indicator_is_rejected(indicator):
    payload = chart_payload()
    payload["chart"]["result"][0]["indicators"]["adjclose"] = indicator
    with pytest.raises(YahooDataStructureError, match="Adj Close"):
        YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)


def test_adjusted_close_array_preserves_missing_row_value():
    payload = chart_payload(adjclose=[None])
    batch = YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)
    assert batch.rows[0].fields["Adj Close"] is None


@pytest.mark.parametrize("field", ["Open", "High", "Low", "Close", "Volume", "Adj Close"])
def test_unrecognized_quote_key_cannot_claim_recognized_field(field):
    payload = chart_payload(quote={"close": [11], field: [99]})
    with pytest.raises(YahooDataStructureError, match="conflicts with a recognized field"):
        YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)


def test_empty_result_is_indeterminate():
    payload = {"chart": {"result": None, "error": None}}
    with pytest.raises(YahooEmptyResultError) as error:
        YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["sp500_index"], WINDOW)
    assert error.value.code == "empty_indeterminate"


def test_provider_errors_are_classified_without_payload():
    for exc, expected in (
        (TimeoutError("private payload"), YahooTimeoutError),
        (ConnectionError("private payload"), YahooNetworkError),
        (RuntimeError("rate limit private payload"), YahooRateLimitError),
    ):
        with pytest.raises(expected) as error:
            YahooAdapter(FakeChartClient(error=exc)).fetch_daily(ASSET_SOURCES["sp500_index"], WINDOW)
        assert "private payload" not in str(error.value)


def test_provider_level_rate_limit_and_unexpected_ticker_are_classified():
    limited = {"chart": {"result": None, "error": {"code": "TooManyRequests", "description": "busy"}}}
    with pytest.raises(YahooRateLimitError):
        YahooAdapter(FakeChartClient(limited)).fetch_daily(ASSET_SOURCES["sp500_index"], WINDOW)
    with pytest.raises(YahooUnexpectedResponseError):
        YahooAdapter(FakeChartClient(chart_payload("WRONG"))).fetch_daily(ASSET_SOURCES["sp500_index"], WINDOW)


def test_incompatible_chart_structure_is_rejected():
    payload = chart_payload("VT", timestamps=[SAMPLE_TIMESTAMP, SAMPLE_TIMESTAMP + 1])
    with pytest.raises(YahooDataStructureError):
        YahooAdapter(FakeChartClient(payload)).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)


def test_yfinance_transport_errors_are_sanitized():
    error = YahooAdapter._translate_error(TimeoutError("private payload"))
    assert isinstance(error, YahooTimeoutError)
    assert "private payload" not in str(error)
    assert isinstance(YahooAdapter._translate_error(ConnectionError("dns")), YahooNetworkError)
    assert isinstance(YahooAdapter._translate_error(RuntimeError("rate limit")), YahooRateLimitError)


def test_raw_batch_exposes_no_pandas_or_yfinance_dataframe():
    batch = YahooAdapter(FakeChartClient(chart_payload("VT"))).fetch_daily(ASSET_SOURCES["world_equity_etf"], WINDOW)
    assert isinstance(batch.rows[0].fields, type(__import__("types").MappingProxyType({})))
    assert not hasattr(batch, "dataframe")


def test_invalid_date_ranges_are_rejected():
    with pytest.raises(ValueError):
        DateRange(date(2026, 10, 3), date(2026, 10, 3))
