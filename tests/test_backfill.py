from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json

import pytest

from backend.market_data import backfill
from backend.market_data.backfill import backfill_asset, export_normalized, read_raw, render_report, summarize, write_raw
from backend.market_data.catalog import ASSETS, ASSET_SOURCES
from backend.market_data.models import DateRange
from backend.market_data.normalizer import normalize
from backend.market_data.yahoo_adapter import (
    RawChartResponse,
    YahooAdapter,
    YahooEmptyResultError,
    YahooRateLimitError,
    YahooTimeoutError,
)


BTC = ASSET_SOURCES["bitcoin_usd"]
WTI = ASSET_SOURCES["wti_future"]
DAY = timedelta(days=1)
START = date(2020, 4, 20)
WINDOW = DateRange(START, START + 3 * DAY)
RETRIEVED = datetime(2026, 11, 8, 13, 0, tzinfo=timezone.utc)


def epoch(day):
    return int(datetime.combine(day, time.min, timezone.utc).timestamp())


class SyntheticChart:
    def __init__(self, days_for, *, close=11, adjusted=True):
        self.days_for = days_for
        self.close = close
        self.adjusted = adjusted
        self.calls = []

    def fetch_chart(self, mapping, window):
        self.calls.append(window)
        days = self.days_for(window)
        count = len(days)
        indicators = {"quote": [{
            "open": [self.close - 1] * count,
            "high": [self.close + 1] * count,
            "low": [self.close - 2] * count,
            "close": [self.close] * count,
            "volume": [None if index % 2 == 0 else 0 for index in range(count)],
        }]}
        if self.adjusted:
            indicators["adjclose"] = [{"adjclose": [self.close + 2] * count}]
        payload = {"chart": {"result": [{
            "meta": {"symbol": mapping.symbol, "exchangeTimezoneName": "UTC", "gmtoffset": 0},
            "timestamp": [epoch(day) for day in days],
            "indicators": indicators,
        }]}}
        return RawChartResponse(payload, payload, RETRIEVED, epoch(window.start), epoch(window.end_exclusive))


def all_days(window):
    return [window.start + offset * DAY for offset in range((window.end_exclusive - window.start).days)]


def test_round_trip_preserves_original_values_and_normalizer_contract(tmp_path):
    chart = SyntheticChart(lambda window: all_days(window) + [window.end_exclusive])
    adapter = YahooAdapter(chart)
    batch = adapter.fetch_daily(BTC, WINDOW)
    path = tmp_path / "daily.json.gz"
    write_raw(path, batch)
    first = path.read_bytes()
    write_raw(path, batch)
    assert path.read_bytes() == first
    loaded = read_raw(path)
    assert loaded == batch
    assert loaded.temporal_metadata["out_of_range_timestamps"] == (epoch(WINDOW.end_exclusive),)
    normalized = normalize(loaded, ASSETS[BTC.asset_id], BTC, WINDOW)
    assert [bar.close for bar in normalized.bars] == [Decimal(11)] * 3
    assert [bar.volume for bar in normalized.bars] == [None, Decimal(0), None]
    assert [bar.adjusted_close for bar in normalized.bars] == [Decimal(13)] * 3
    assert [item.code for item in normalized.rejected] == ["outside_requested_range"]


def test_wti_negative_close_and_absent_adj_close_stay_in_raw(tmp_path):
    chart = SyntheticChart(all_days, close=-37.63, adjusted=False)
    result = backfill_asset(YahooAdapter(chart), tmp_path, WTI, WINDOW)
    archived = next(iter(result.batches.values()))
    assert archived.rows[0].fields["Close"] == -37.63
    assert "Adj Close" not in archived.rows[0].fields
    assert not summarize(result, WINDOW).accepted_dates
    assert summarize(result, WINDOW).rejected == {"temporal_convention_unverified": 3}


def test_retries_network_and_rate_limits_then_reuses_exact_files(tmp_path):
    failures = [YahooRateLimitError("synthetic"), YahooTimeoutError("synthetic")]

    def available(window):
        if failures:
            raise failures.pop(0)
        return all_days(window)

    chart = SyntheticChart(available)
    adapter = YahooAdapter(chart)
    pauses = []
    initial = backfill_asset(adapter, tmp_path, BTC, WINDOW, pause=pauses.append)
    assert initial.requests == 3
    assert pauses == [8, 8]
    path = next(iter(initial.batches))
    digest = sha256(path.read_bytes()).digest()

    repeat = backfill_asset(adapter, tmp_path, BTC, WINDOW, pause=pauses.append)
    assert repeat.cache_hits == 1
    assert repeat.requests == 0
    assert chart.calls == [WINDOW, WINDOW, WINDOW]
    assert list(tmp_path.rglob("*.json.gz")) == [path]
    assert sha256(path.read_bytes()).digest() == digest


def test_recovers_missing_date_without_discarding_duplicate_or_boundary_evidence(tmp_path):
    gap = DateRange(START + DAY, START + 2 * DAY)

    def missing_middle(window):
        if window == WINDOW:
            return [START, START, START + 2 * DAY, WINDOW.end_exclusive]
        return all_days(window)

    chart = SyntheticChart(missing_middle)
    result = backfill_asset(YahooAdapter(chart), tmp_path, BTC, WINDOW)
    summary = summarize(result, WINDOW)
    assert chart.calls == [WINDOW, gap]
    assert len(result.batches) == 2
    assert summary.dates == set(all_days(WINDOW))
    assert summary.labels.total() == 5
    assert summary.outside == 1
    assert summary.accepted_dates[START] == 2
    assert summary.rejected == {"outside_requested_range": 1}
    _, attention = render_report([result], WINDOW, "synthetic")
    assert attention


def test_fallback_reuses_cached_children_and_retries_only_failed_child(tmp_path):
    wide = DateRange(START, START + 367 * DAY)
    first = DateRange(START, START + 366 * DAY)
    last = DateRange(first.end_exclusive, wide.end_exclusive)
    last_available = False

    def bounded_only(window):
        if window == wide or (window == last and not last_available):
            raise YahooEmptyResultError("synthetic")
        return all_days(window)

    chart = SyntheticChart(bounded_only)
    adapter = YahooAdapter(chart)
    initial = backfill_asset(adapter, tmp_path, BTC, wide, pause=lambda _: None)
    assert initial.failures == {wide: "empty_indeterminate", last: "empty_indeterminate"}
    assert chart.calls == [wide, wide, wide, first, last, last, last]
    assert len(initial.batches) == 1

    chart.calls.clear()
    last_available = True
    retry = backfill_asset(adapter, tmp_path, BTC, wide, pause=lambda _: None)
    assert chart.calls == [last]
    assert retry.cache_hits == 1
    assert len(retry.batches) == 2
    assert summarize(retry, wide).dates == set(all_days(wide))

    chart.calls.clear()
    repeat = backfill_asset(adapter, tmp_path, BTC, wide, pause=lambda _: None)
    assert not chart.calls
    assert repeat.cache_hits == 2
    assert repeat.batches == retry.batches


def test_corrupt_or_misidentified_cache_is_preserved(tmp_path):
    chart = SyntheticChart(all_days)
    adapter = YahooAdapter(chart)
    initial = backfill_asset(adapter, tmp_path, BTC, WINDOW)
    path, batch = next(iter(initial.batches.items()))
    write_raw(path, replace(batch, ticker="VT"))
    mismatched = path.read_bytes()
    with pytest.raises(ValueError, match="identity mismatch"):
        backfill_asset(adapter, tmp_path, BTC, WINDOW)
    assert path.read_bytes() == mismatched

    path.write_bytes(b"corrupt")
    with pytest.raises(OSError):
        backfill_asset(adapter, tmp_path, BTC, WINDOW)
    assert path.read_bytes() == b"corrupt"
    assert chart.calls == [WINDOW]


def test_offline_cli_reprocesses_original_equity_archive_without_network_or_mutation(tmp_path, monkeypatch):
    mapping = ASSET_SOURCES["world_equity_etf"]
    archived_mapping = replace(mapping, source_convention="unknown")
    batch = YahooAdapter(SyntheticChart(all_days)).fetch_daily(mapping, WINDOW)
    zone = "America/New_York"
    archive = replace(
        batch,
        mapping=archived_mapping,
        rows=tuple(
            replace(row, original_label=row.original_label + 13 * 3600 + 30 * 60)
            for row in batch.rows
        ),
        temporal_metadata={**batch.temporal_metadata, "index_timezone": zone},
    )
    path = tmp_path / mapping.asset_id / f"{WINDOW.start}_{WINDOW.end_exclusive}.json.gz"
    write_raw(path, archive)
    raw_before = path.read_bytes()
    stage_one_report = tmp_path / "report.md"
    stage_one_report.write_text("Stage 1 evidence", encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        pytest.fail("offline reprocessing must not instantiate YahooAdapter")

    monkeypatch.setattr(backfill, "YahooAdapter", forbidden)
    assert backfill.main([
        "--offline", "--output", str(tmp_path),
        "--start", str(WINDOW.start), "--end-exclusive", str(WINDOW.end_exclusive),
    ]) == 1  # Only VT was archived in this fixture.

    report = (tmp_path / "report-1c.md").read_text(encoding="utf-8")
    assert "| VT | 2020-04-20 | 2020-04-22 | 3 | 3 |" in report
    assert "| VT | 0 | temporal_convention_unverified ×3 |" in report
    assert "cache_missing" in report
    assert path.read_bytes() == raw_before
    assert read_raw(path).mapping == archived_mapping
    assert stage_one_report.read_text(encoding="utf-8") == "Stage 1 evidence"
    assert not (tmp_path / "normalized").exists()


def test_normalized_export_is_deterministic_with_raw_provenance_and_rejection(tmp_path):
    chart = SyntheticChart(lambda window: all_days(window) + [window.end_exclusive])
    result = backfill_asset(YahooAdapter(chart), tmp_path, BTC, WINDOW)
    raw_path, = result.batches
    raw = raw_path.read_bytes()
    path = export_normalized(result, tmp_path, WINDOW)
    first = path.read_bytes()
    assert path.parent.parent == tmp_path / "normalized"
    assert export_normalized(result, tmp_path, WINDOW) == path
    assert path.read_bytes() == first
    assert raw_path.read_bytes() == raw

    with gzip.open(path, "rt", encoding="utf-8") as source:
        payload = json.load(source)
    batch, = payload["batches"]
    assert batch["raw_file"] == raw_path.relative_to(tmp_path).as_posix()
    assert batch["raw_sha256"] == sha256(raw).hexdigest()
    assert [item["bar"]["close"] for item in batch["accepted"]] == ["11"] * 3
    assert [item["bar"]["volume"] for item in batch["accepted"]] == [None, "0", None]
    assert [item["raw_position"] for item in batch["accepted"]] == [0, 1, 2]
    assert batch["rejected"] == [{
        "raw_position": 3, "original_label": epoch(WINDOW.end_exclusive),
        "provider_local_date": WINDOW.end_exclusive.isoformat(), "code": "outside_requested_range",
    }]
