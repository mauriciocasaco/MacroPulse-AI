"""Raw Yahoo chart adapter using a per-call yfinance transport client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Callable, Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import ASSET_SOURCES
from .models import AssetSource, DateRange, RawBatch, RawRow

ADAPTER_VERSION = "1.0.0"
YFINANCE_VERSION = "1.8.0"


class YahooAdapterError(RuntimeError):
    """Base error whose code is safe to report without provider payloads."""

    code = "provider_error"


class YahooNetworkError(YahooAdapterError):
    code = "network_error"


class YahooTimeoutError(YahooNetworkError):
    code = "timeout"


class YahooRateLimitError(YahooAdapterError):
    code = "rate_limited"


class YahooUnexpectedResponseError(YahooAdapterError):
    code = "unexpected_response"


class YahooEmptyResultError(YahooAdapterError):
    code = "empty_indeterminate"


class YahooDataStructureError(YahooAdapterError):
    code = "incompatible_structure"


class YahooDependencyError(YahooAdapterError):
    code = "dependency_unavailable"


class RawChartClient(Protocol):
    def fetch_chart(self, mapping: AssetSource, window: DateRange) -> "RawChartResponse": ...


@dataclass(frozen=True)
class RawChartResponse:
    payload: Mapping[str, object]
    metadata_payload: Mapping[str, object]
    received_at: datetime
    period1: int
    period2: int


class _CapturedYfData:
    """Construct an unregistered YfData instance to avoid its process singleton."""

    def __init__(self, session: object) -> None:
        from yfinance.data import YfData

        class IsolatedYfData(YfData):
            def _save_cookie_curlCffi(self) -> bool:
                return False

            def _load_cookie_curlCffi(self) -> bool:
                return False

        self._instance = object.__new__(IsolatedYfData)
        IsolatedYfData.__init__(self._instance, session=session)

    def get(self, url: str, params: Mapping[str, object], timeout: int) -> object:
        return self._instance.get(url=url, params=dict(params), timeout=timeout)


class _YFinanceChartClient:
    """Pinned yfinance 1.8.0 transport with raw chart response capture."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], object] | None = None,
        data_factory: Callable[[object], _CapturedYfData] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._data_factory = data_factory
        self._base_url = base_url

    def fetch_chart(self, mapping: AssetSource, window: DateRange) -> RawChartResponse:
        try:
            installed_version = version("yfinance")
        except PackageNotFoundError as exc:
            raise YahooDependencyError("yfinance is not installed") from exc
        if installed_version != YFINANCE_VERSION:
            raise YahooDependencyError("yfinance version does not match the supported adapter version")

        session_factory = self._session_factory or self._default_session_factory
        data_factory = self._data_factory or _CapturedYfData
        session = session_factory()
        try:
            data = data_factory(session)
            base_url = self._base_url or self._default_base_url()
            chart_url = f"{base_url}/v8/finance/chart/{mapping.symbol}"

            # Yahoo requires exchange-local midnight bounds for daily sessions.
            metadata_response = data.get(
                chart_url,
                {"range": "1d", "interval": "1d", "includePrePost": False, "events": "div,splits,capitalGains"},
                timeout=20,
            )
            metadata_payload = self._decode_response(metadata_response)
            metadata = _chart_metadata(metadata_payload, mapping.symbol)
            timezone_name = metadata.get("exchangeTimezoneName")
            if not isinstance(timezone_name, str) or not timezone_name:
                raise YahooDataStructureError("Yahoo response did not include an exchange timezone")
            try:
                exchange_zone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise YahooDataStructureError("Yahoo returned an unknown exchange timezone") from exc

            start_epoch = int(datetime.combine(window.start, time.min, exchange_zone).timestamp())
            end_epoch = int(datetime.combine(window.end_exclusive, time.min, exchange_zone).timestamp())
            params = {
                "period1": start_epoch,
                "period2": end_epoch,
                "interval": "1d",
                "includePrePost": False,
                "events": "div,splits,capitalGains",
            }
            response = data.get(chart_url, params, timeout=20)
            received_at = datetime.now(timezone.utc)
            payload = self._decode_response(response)
            return RawChartResponse(payload, metadata_payload, received_at, start_epoch, end_epoch)
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _default_session_factory() -> object:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise YahooDependencyError("yfinance transport dependencies are not installed") from exc
        return curl_requests.Session(impersonate="chrome")

    @staticmethod
    def _default_base_url() -> str:
        from yfinance import const

        return const._BASE_URL_

    @staticmethod
    def _decode_response(response: object) -> Mapping[str, object]:
        status = getattr(response, "status_code", None)
        if type(status) is int and status >= 400:
            if status == 429:
                raise YahooRateLimitError("Yahoo Finance rate limit was reached")
            raise YahooUnexpectedResponseError(f"Yahoo Finance returned HTTP status {status}")
        try:
            payload = response.json()
        except Exception:
            raise YahooDataStructureError("Yahoo Finance returned invalid JSON") from None
        if not isinstance(payload, Mapping):
            raise YahooDataStructureError("Yahoo chart response is malformed")
        return payload


class YahooAdapter:
    """Fetch raw provider chart fields without yfinance's lossy DataFrame steps."""

    def __init__(self, chart_client: RawChartClient | None = None) -> None:
        self._chart_client = chart_client or _YFinanceChartClient()

    def fetch_daily(self, mapping: AssetSource, window: DateRange) -> RawBatch:
        if mapping.source_id != "yahoo_finance":
            raise ValueError("mapping source_id must be yahoo_finance")
        if mapping.asset_id not in ASSET_SOURCES or ASSET_SOURCES[mapping.asset_id] != mapping:
            raise ValueError("unknown or mismatched Yahoo asset mapping")
        if not isinstance(window, DateRange):
            raise ValueError("window must be a valid DateRange")

        try:
            response = self._chart_client.fetch_chart(mapping, window)
        except YahooAdapterError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from None

        if not isinstance(response, RawChartResponse):
            raise YahooDataStructureError("Yahoo client returned an unsupported result")
        _chart_metadata(response.metadata_payload, mapping.symbol)
        rows, temporal_metadata = self._extract(
            response.payload, mapping.symbol, response.period1, response.period2
        )
        if not rows:
            raise YahooEmptyResultError("Yahoo returned no rows; cause is indeterminate")
        return RawBatch(
            asset_id=mapping.asset_id,
            mapping=mapping,
            provider="yahoo_finance",
            ticker=mapping.symbol,
            requested_range=window,
            interval="1d",
            retrieved_at=response.received_at,
            adapter_version=ADAPTER_VERSION,
            rows=rows,
            temporal_metadata=temporal_metadata,
            provider_version=YFINANCE_VERSION,
            warnings=self._warnings(
                mapping,
                rows,
                response.period1,
                response.period2,
                temporal_metadata["out_of_range_timestamps"],
            ),
        )

    @staticmethod
    def _translate_error(exc: Exception) -> YahooAdapterError:
        name = type(exc).__name__.casefold()
        message = str(exc).casefold()
        if "timeout" in name or "timed out" in message:
            return YahooTimeoutError("Yahoo Finance request timed out")
        if "ratelimit" in name or "rate limit" in message or "too many requests" in message:
            return YahooRateLimitError("Yahoo Finance rate limit was reached")
        if "connection" in name or "network" in name or "connection" in message or "dns" in name:
            return YahooNetworkError("Yahoo Finance network request failed")
        return YahooAdapterError("Yahoo Finance provider request failed")

    @classmethod
    def _extract(
        cls,
        payload: Mapping[str, object],
        symbol: str,
        period1: int,
        period2: int,
    ) -> tuple[tuple[RawRow, ...], dict[str, object]]:
        metadata = _chart_metadata(payload, symbol)
        chart = payload.get("chart")
        if not isinstance(chart, Mapping):
            raise YahooDataStructureError("Yahoo chart response is malformed")
        result = chart.get("result")
        if not result:
            raise YahooEmptyResultError("Yahoo returned an empty result; cause is indeterminate")
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], Mapping):
            raise YahooUnexpectedResponseError("Yahoo returned an unexpected result count")

        item = result[0]
        returned_symbol = metadata.get("symbol")
        if returned_symbol != symbol:
            raise YahooUnexpectedResponseError("Yahoo returned a different ticker")
        timestamps = item.get("timestamp")
        indicators = item.get("indicators")
        if not isinstance(timestamps, list) or not isinstance(indicators, Mapping):
            raise YahooDataStructureError("Yahoo chart response lacks timestamps or indicators")
        quotes = indicators.get("quote")
        if not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], Mapping):
            raise YahooDataStructureError("Yahoo chart response lacks one-symbol quote data")

        field_names = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        reserved_names = set(field_names.values()) | {"Adj Close"}
        arrays: dict[str, list[object]] = {}
        for source_name, values in quotes[0].items():
            if not isinstance(source_name, str):
                raise YahooDataStructureError("Yahoo returned a non-string quote field")
            if source_name in reserved_names:
                raise YahooDataStructureError("Yahoo returned a quote field that conflicts with a recognized field")
            field_name = field_names.get(source_name, source_name)
            arrays[field_name] = _checked_array(values, len(timestamps), field_name)
        if "adjclose" in indicators:
            adjusted = indicators["adjclose"]
            if (
                not isinstance(adjusted, list)
                or len(adjusted) != 1
                or not isinstance(adjusted[0], Mapping)
                or "adjclose" not in adjusted[0]
            ):
                raise YahooDataStructureError("Yahoo returned a malformed Adj Close indicator")
            arrays["Adj Close"] = _checked_array(adjusted[0]["adjclose"], len(timestamps), "Adj Close")

        rows: list[RawRow] = []
        out_of_range_timestamps: list[int] = []
        for index, timestamp in enumerate(timestamps):
            if type(timestamp) is not int:
                raise YahooDataStructureError("Yahoo returned a non-integer chart timestamp")
            if timestamp < period1 or timestamp >= period2:
                out_of_range_timestamps.append(timestamp)
            fields = {name: values[index] for name, values in arrays.items()}
            rows.append(RawRow(original_label=timestamp, fields=fields))

        temporal_metadata = {
            "index_timezone": metadata.get("exchangeTimezoneName"),
            "index_timezone_available": bool(metadata.get("exchangeTimezoneName")),
            "timezone_offset_seconds": metadata.get("gmtoffset"),
            "provider_session_label_convention": "unverified",
            "daily_cutoff": "unverified",
            "requested_period1": period1,
            "requested_period2": period2,
            "out_of_range_timestamps": tuple(out_of_range_timestamps),
        }
        return tuple(rows), temporal_metadata

    @staticmethod
    def _warnings(
        mapping: AssetSource,
        rows: Iterable[RawRow],
        period1: int,
        period2: int,
        out_of_range: tuple[int, ...] = (),
    ) -> tuple[str, ...]:
        warnings = ["daily session label and cutoff convention are unverified; raw Unix timestamps are retained"]
        if mapping.instrument_type == "future":
            warnings.append("futures continuous-symbol rolling method is unknown")
        fields = {name for row in rows for name in row.fields}
        if "Adj Close" not in fields:
            warnings.append("Adj Close was absent from the provider response")
        if "Volume" not in fields:
            warnings.append("Volume was absent from the provider response")
        if out_of_range:
            warnings.append(
                "Yahoo returned chart timestamps outside the requested epoch range "
                f"[{period1},{period2}); received {len(out_of_range)} outside value(s) "
                f"{list(out_of_range)}. Raw rows are retained."
            )
        return tuple(warnings)


def _chart_metadata(payload: object, symbol: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise YahooDataStructureError("Yahoo chart response is malformed")
    chart = payload.get("chart")
    if not isinstance(chart, Mapping):
        raise YahooDataStructureError("Yahoo chart response is malformed")
    if chart.get("error"):
        _raise_chart_provider_error(chart["error"])
    results = chart.get("result")
    if not results:
        raise YahooEmptyResultError("Yahoo returned no chart metadata; cause is indeterminate")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], Mapping):
        raise YahooUnexpectedResponseError("Yahoo returned an unexpected result count")
    metadata = results[0].get("meta")
    if not isinstance(metadata, Mapping):
        raise YahooDataStructureError("Yahoo chart response lacks metadata")
    returned_symbol = metadata.get("symbol")
    if returned_symbol != symbol:
        raise YahooUnexpectedResponseError("Yahoo returned a different ticker")
    return metadata


def _raise_chart_provider_error(error: object) -> None:
    code = error.get("code", "") if isinstance(error, Mapping) else ""
    description = error.get("description", "") if isinstance(error, Mapping) else ""
    error_text = f"{code} {description}".casefold()
    if "rate" in error_text or "too many" in error_text or "toomany" in error_text:
        raise YahooRateLimitError("Yahoo Finance rate limit was reached")
    raise YahooUnexpectedResponseError("Yahoo Finance returned a provider error")


def _checked_array(value: object, expected_length: int, field: str) -> list[object]:
    if not isinstance(value, list) or len(value) != expected_length:
        raise YahooDataStructureError(f"Yahoo {field} values do not match chart timestamps")
    if any(item is not None and type(item) not in (str, int, float, bool) for item in value):
        raise YahooDataStructureError(f"Yahoo {field} values contain a non-scalar value")
    return value
