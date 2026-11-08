from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data.normalizer import NormalizationStructureError, financial_rejection


WINDOW = DateRange(date(2026, 10, 1), date(2026, 11, 1))
FIELDS = {"Open": 10, "High": 12, "Low": 9, "Close": 11, "Adj Close": 10.5, "Volume": 0}


def fixture_batch(asset_id="world_equity_etf", rows=None, *, zone_name="America/New_York", verified=True):
    base_mapping = ASSET_SOURCES[asset_id]
    convention = base_mapping.source_convention if asset_id == "bitcoin_usd" and verified else (
        f"verified:yahoo_exchange_date:v1:{zone_name}" if verified else "unknown"
    )
    mapping = replace(base_mapping, source_convention=convention)
    default_label = datetime(2026, 10, 3, tzinfo=timezone.utc) if asset_id == "bitcoin_usd" else datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    return RawBatch(
        asset_id=asset_id,
        mapping=mapping,
        provider="yahoo_finance",
        ticker=mapping.symbol,
        requested_range=WINDOW,
        interval="1d",
        retrieved_at=datetime(2026, 11, 1, 12, tzinfo=timezone.utc),
        adapter_version="1.0.0",
        rows=tuple(rows if rows is not None else [RawRow(int(default_label.timestamp()), FIELDS)]),
        temporal_metadata={
            "index_timezone": zone_name,
            "provider_session_label_convention": convention if verified and asset_id != "bitcoin_usd" else "unverified",
        },
        provider_version="1.8.0",
    ), mapping


def normalized(rows=None, *, asset_id="world_equity_etf", zone_name="America/New_York", verified=True):
    batch, mapping = fixture_batch(asset_id, rows, zone_name=zone_name, verified=verified)
    return normalize(batch, ASSETS[asset_id], mapping, WINDOW)


@pytest.mark.parametrize("asset_id,zone_name", [
    ("gold_future", "America/Chicago"),
    ("wti_future", "America/Chicago"),
    ("sp500_index", "America/New_York"),
    ("world_equity_etf", "America/New_York"),
    ("emerging_markets_etf", "America/New_York"),
    ("bitcoin_usd", "UTC"),
])
def test_catalog_assets_normalize_to_decimal_and_preserve_mapping(asset_id, zone_name):
    result = normalized(asset_id=asset_id, zone_name=zone_name)
    assert len(result.bars) == 1
    bar = result.bars[0]
    assert bar.asset_id == asset_id
    assert bar.symbol == ASSET_SOURCES[asset_id].symbol
    assert bar.close == Decimal("11")
    assert bar.volume == Decimal("0")
    assert bar.retrieved_at.tzinfo == timezone.utc
    assert bar.session_status == "unknown"
    assert bar.quality_status == "complete"
    assert bar.adapter_version == "1.0.0"
    assert result.provenance["provider_version"] == "1.8.0"


def test_unverified_yahoo_temporal_convention_withholds_rows_with_diagnostics():
    result = normalized(verified=False)
    assert not result.bars
    assert [item.code for item in result.rejected] == ["temporal_convention_unverified"]
    assert any("unverified" in warning for warning in result.warnings)


def test_epoch_timestamps_use_exchange_iana_zone_across_dst():
    rows = [
        RawRow(int(datetime(2026, 10, 31, 23, tzinfo=timezone.utc).timestamp()), FIELDS),
        RawRow(int(datetime(2026, 11, 1, 23, tzinfo=timezone.utc).timestamp()), FIELDS),
    ]
    batch, mapping = fixture_batch(rows=rows)
    window = DateRange(date(2026, 10, 31), date(2026, 11, 2))
    batch = replace(batch, requested_range=window)
    result = normalize(batch, ASSETS[mapping.asset_id], mapping, window)
    assert [bar.bar_date for bar in result.bars] == [date(2026, 10, 31), date(2026, 11, 1)]
    assert ZoneInfo("America/New_York").utcoffset(datetime(2026, 11, 1, 12)) != ZoneInfo("America/New_York").utcoffset(datetime(2026, 10, 31, 12))


def test_bitcoin_upper_exclusive_boundary_is_rejected():
    row = RawRow(int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()), FIELDS)
    result = normalized([row], asset_id="bitcoin_usd", zone_name="UTC")
    assert not result.bars
    assert result.rejected[0].code == "outside_requested_range"


def test_local_weekend_bar_is_preserved_for_bitcoin():
    row = RawRow(int(datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp()), FIELDS)
    result = normalized([row], asset_id="bitcoin_usd", zone_name="UTC")
    assert result.bars[0].bar_date == date(2026, 10, 3)
    assert any("verifies UTC date labels only" in warning for warning in result.warnings)


def test_bitcoin_accepts_only_utc_midnight_chart_timestamps():
    labels = (
        date(2026, 10, 5),
        datetime(2026, 10, 5, tzinfo=timezone.utc),
        "2026-10-05",
    )
    for label in labels:
        result = normalized([RawRow(label, FIELDS)], asset_id="bitcoin_usd", zone_name="UTC")
        assert not result.bars
        assert result.rejected[0].code == "unverified_bitcoin_label"


def test_bitcoin_convention_is_bound_to_yahoo_finance_source():
    mapping = replace(ASSET_SOURCES["bitcoin_usd"], source_id="other_source")
    timestamp = int(datetime(2026, 10, 5, tzinfo=timezone.utc).timestamp())
    batch = RawBatch(
        asset_id="bitcoin_usd", mapping=mapping, provider="other_source", ticker="BTC-USD",
        requested_range=WINDOW, interval="1d", retrieved_at=datetime(2026, 11, 1, 12, tzinfo=timezone.utc), adapter_version="1.0.0",
        rows=(RawRow(timestamp, FIELDS),), temporal_metadata={"index_timezone": "UTC"},
    )
    result = normalize(batch, ASSETS["bitcoin_usd"], mapping, WINDOW)
    assert not result.bars
    assert result.rejected[0].code == "temporal_convention_unverified"


@pytest.mark.parametrize("evidence,expected", [
    ("closed", "closed"),
    ("provisional", "provisional"),
    ("active", "unknown"),
    (None, "unknown"),
])
def test_recognized_session_evidence_is_preserved_without_inference(evidence, expected):
    fields = dict(FIELDS)
    if evidence is not None:
        fields["session_status"] = evidence
    batch, mapping = fixture_batch(rows=[RawRow(
        int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp()), fields,
    )])
    if evidence is None:
        batch = replace(batch, temporal_metadata={
            **batch.temporal_metadata, "session_status": "provisional",
        })
        expected = "provisional"
    result = normalize(batch, ASSETS[mapping.asset_id], mapping, WINDOW)
    assert result.bars[0].session_status == expected
    if evidence in ("closed", "provisional"):
        assert not any("session status could not be determined" in warning for warning in result.warnings)


def test_null_optional_fields_volume_zero_adjustment_and_partial_quality():
    row = RawRow(int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp()), {
        "Open": 10, "High": None, "Low": 9, "Close": 11, "Adj Close": None, "Volume": 0,
    })
    result = normalized([row])
    assert len(result.bars) == 1
    bar = result.bars[0]
    assert bar.high is None and bar.adjusted_close is None
    assert bar.adjusted_close_basis is None and bar.volume == Decimal("0")
    assert bar.quality_status == "partial"


@pytest.mark.parametrize("fields,code", [
    ({"Open": 10, "High": 12, "Low": 9, "Close": None}, "missing_close"),
    ({"Open": 10, "High": float("inf"), "Low": 9, "Close": None}, "missing_close"),
    ({"Open": 10, "High": 12, "Low": 9, "Adj Close": 11}, "missing_close"),
    ({"Open": 10, "High": 8, "Low": 9, "Close": 10}, "inconsistent_high_low"),
    ({"Open": 12, "High": 11, "Low": 9, "Close": 10}, "inconsistent_high"),
    ({"Open": 10, "High": 12, "Low": 11, "Close": 10}, "inconsistent_low"),
    ({"Open": 10, "High": 12, "Low": 9, "Close": 10, "Volume": -1}, "negative_volume"),
    ({"Open": 10, "High": 12, "Low": 9, "Close": float("nan")}, "invalid_close"),
    ({"Open": 10, "High": float("inf"), "Low": 9, "Close": 10}, "invalid_high"),
    ({"Open": 10, "High": 12, "Low": 9, "Close": 10, "Adj Close": float("-inf")}, "invalid_adj_close"),
])
def test_invalid_rows_are_rejected_by_stable_code(fields, code):
    row = RawRow(int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp()), fields)
    result = normalized([row])
    assert not result.bars
    assert result.rejected[0].position == 0
    assert result.rejected[0].code == code


def test_negative_wti_is_valid_but_other_initial_assets_are_strictly_positive():
    wti = normalized([RawRow(int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp()), {
        "Open": -5, "High": 2, "Low": -8, "Close": -3, "Volume": 0,
    })], asset_id="wti_future", zone_name="America/Chicago")
    assert wti.bars[0].close == Decimal("-3")
    equity = normalized([RawRow(int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp()), {
        "Open": 10, "High": 12, "Low": 9, "Close": 0,
    })])
    assert equity.rejected[0].code == "non_positive_price"


def test_row_diagnostics_keep_valid_neighbor_and_structural_mismatch_raises():
    timestamp = int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp())
    result = normalized([RawRow(timestamp, FIELDS), RawRow(timestamp, {"Close": None, "Adj Close": 11})])
    assert len(result.bars) == 1
    assert result.rejected[0].code == "missing_close"
    batch, mapping = fixture_batch()
    with pytest.raises(NormalizationStructureError):
        normalize(replace(batch, ticker="wrong"), ASSETS[mapping.asset_id], mapping, WINDOW)


def test_naive_timestamp_is_rejected():
    result = normalized([RawRow(datetime(2026, 10, 3, 12), FIELDS)])
    assert result.rejected[0].code == "invalid_timestamp"


def test_session_status_stays_unknown_without_provider_evidence():
    result = normalized(asset_id="bitcoin_usd", zone_name="UTC")
    assert result.bars[0].session_status == "unknown"
    assert any("session status could not be determined" in warning for warning in result.warnings)


def _reviewed_equity(asset_id, rows, window, *, archived=False, zone="America/New_York"):
    batch, _ = fixture_batch(asset_id=asset_id, rows=rows)
    mapping = ASSET_SOURCES[asset_id]
    raw_mapping = replace(mapping, source_convention="unknown") if archived else mapping
    batch = replace(
        batch, mapping=raw_mapping, requested_range=window,
        temporal_metadata={"index_timezone": zone, "provider_session_label_convention": "unverified"},
    )
    return batch, mapping


@pytest.mark.parametrize("asset_id", ["sp500_index", "world_equity_etf", "emerging_markets_etf"])
@pytest.mark.parametrize("archived", [False, True])
def test_us_equity_open_labels_across_dst(asset_id, archived):
    window = DateRange(date(2026, 3, 6), date(2026, 11, 3))
    # Actual labels from both DST transitions, with synthetic prices.
    epochs = [1772807400, 1773063000, 1793367000, 1793629800]
    rows = [RawRow(epoch, FIELDS) for epoch in epochs]
    batch, mapping = _reviewed_equity(asset_id, rows, window, archived=archived)
    result = normalize(batch, ASSETS[asset_id], mapping, window)
    assert [bar.bar_date for bar in result.bars] == [
        date(2026, 3, 6), date(2026, 3, 9), date(2026, 10, 30), date(2026, 11, 2),
    ]
    assert all(bar.session_status == "unknown" for bar in result.bars)
    assert all(bar.retrieved_at == batch.retrieved_at for bar in result.bars)
    assert result.provenance["archived_source_convention"] == batch.mapping.source_convention
    assert result.provenance["source_convention"] == mapping.source_convention
    assert result.provenance["temporal_convention_status"] == "verified_for_us_equity_session_dates"
    assert batch.temporal_metadata["provider_session_label_convention"] == "unverified"


def _new_york_open(day):
    return int(datetime(day.year, day.month, day.day, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp())


def test_equity_sessions_exclude_full_closures_and_weekends_but_keep_early_close():
    days = [date(2025, 1, 9), date(2025, 7, 4), date(2025, 11, 28), date(2025, 11, 29)]
    rows = [RawRow(_new_york_open(day), FIELDS) for day in days]
    window = DateRange(days[0], date(2025, 11, 30))
    batch, mapping = _reviewed_equity("world_equity_etf", rows, window)
    result = normalize(batch, ASSETS[mapping.asset_id], mapping, window)
    assert [bar.bar_date for bar in result.bars] == [days[2]]
    assert [(item.position, item.code) for item in result.rejected] == [
        (0, "non_session_date"), (1, "non_session_date"), (3, "non_session_date"),
    ]


def test_equity_rejects_invalid_shape_wrong_clock_or_exclusive_end():
    first, end = date(2019, 11, 8), date(2019, 11, 11)
    window = DateRange(first, end)
    labels = [_new_york_open(first), first, _new_york_open(first) + 3600, _new_york_open(end)]
    rows = [RawRow(label, FIELDS) for label in labels]
    batch, mapping = _reviewed_equity("sp500_index", rows, window)
    result = normalize(batch, ASSETS[mapping.asset_id], mapping, window)
    assert [bar.bar_date for bar in result.bars] == [first]
    assert [item.code for item in result.rejected] == [
        "unverified_us_equity_label", "unverified_us_equity_label", "outside_requested_range",
    ]


def test_equity_calendar_does_not_claim_future_dates_and_checks_reported_zone():
    window = DateRange(date(2026, 11, 6), date(2026, 11, 10))
    rows = [RawRow(1793975400, FIELDS), RawRow(_new_york_open(date(2026, 11, 9)), FIELDS)]
    batch, mapping = _reviewed_equity("emerging_markets_etf", rows, window)
    asset = ASSETS[mapping.asset_id]
    result = normalize(batch, asset, mapping, window)
    assert [bar.bar_date for bar in result.bars] == [window.start]
    assert [item.code for item in result.rejected] == ["outside_verified_calendar"]
    wrong_zone = replace(batch, temporal_metadata={"index_timezone": "UTC"})
    assert [item.code for item in normalize(wrong_zone, asset, mapping, window).rejected] == [
        "temporal_convention_unverified", "temporal_convention_unverified",
    ]


@pytest.mark.parametrize("asset_id", ["gold_future", "wti_future"])
def test_futures_overnight_labels_remain_unverified(asset_id):
    asset = ASSETS[asset_id]
    mapping = replace(ASSET_SOURCES[asset_id], source_convention="unknown")
    # Sunday evening in Chicago can be part of a Monday session.
    sunday_evening = int(datetime(2026, 3, 8, 17, tzinfo=ZoneInfo("America/Chicago")).timestamp())
    prices = {"Open": -10, "High": 3, "Low": -40, "Close": -37.63, "Volume": 0}
    rows = [RawRow(sunday_evening, prices), RawRow(sunday_evening, {"Adj Close": 10})]
    batch, _ = fixture_batch(asset_id, rows, verified=False)
    window = DateRange(date(2026, 3, 8), date(2026, 3, 10))
    batch = replace(batch, requested_range=window)
    result = normalize(batch, asset, mapping, window)
    assert not result.bars
    assert [item.code for item in result.rejected] == ["temporal_convention_unverified"] * 2
    assert financial_rejection(rows[0].fields, asset) == (None if asset_id == "wti_future" else "non_positive_price")
    assert financial_rejection(rows[1].fields, asset) == "missing_close"


def _futures_batch(asset_id, rows, window, *, zone="America/New_York"):
    batch, archived = fixture_batch(asset_id, rows, verified=False, zone_name=zone)
    return replace(batch, requested_range=window), archived, ASSET_SOURCES[asset_id]


@pytest.mark.parametrize("asset_id", ["gold_future", "wti_future"])
def test_futures_yahoo_published_dates_keep_holidays_and_both_clock_shapes(asset_id):
    # Archive epochs from an early close, a holiday, and ordinary dates around DST.
    labels = [1575037800, 1751601600, 1772773200, 1773028800]
    days = [date(2019, 11, 29), date(2025, 7, 4), date(2026, 3, 6), date(2026, 3, 9)]
    synthetic_day = date(2026, 3, 10)
    labels.append(_new_york_open(synthetic_day))
    days.append(synthetic_day)
    window = DateRange(days[0], date(2026, 3, 11))
    batch, archived, mapping = _futures_batch(asset_id, [RawRow(label, FIELDS) for label in labels], window)
    result = normalize(batch, ASSETS[asset_id], mapping, window)

    assert not result.rejected
    assert [bar.bar_date for bar in result.bars] == days
    assert all(bar.rolling_method == "unknown" for bar in result.bars)
    assert all(bar.exchange_trade_date is None and bar.settlement_time is None for bar in result.bars)
    assert all(bar.availability_time is None and bar.retrieved_at == batch.retrieved_at for bar in result.bars)
    assert result.provenance["temporal_convention_status"] == "verified_for_yahoo_futures_published_dates"
    assert result.provenance["archived_source_convention"] == archived.source_convention == "unknown"
    assert batch.temporal_metadata["provider_session_label_convention"] == "unverified"


def test_futures_reject_unverified_shapes_and_respect_exclusive_end():
    days = [date(2026, 3, 6), date(2026, 3, 7), date(2026, 3, 9)]
    window = DateRange(days[0], days[-1])
    labels = [1772773200, days[0], _new_york_open(days[0]) + 1, _new_york_open(days[1]), 1773028800]
    asset = ASSETS["gold_future"]
    rows = [RawRow(label, FIELDS) for label in labels]
    batch, _, mapping = _futures_batch(asset.id, rows, window)
    normalized = normalize(batch, asset, mapping, window)
    assert [bar.bar_date for bar in normalized.bars] == [window.start]
    assert [item.code for item in normalized.rejected] == [
        "unverified_yahoo_futures_label", "unverified_yahoo_futures_label",
        "unverified_yahoo_futures_label", "outside_requested_range",
    ]
    wrong_zone = replace(batch, temporal_metadata={"index_timezone": "America/Chicago"})
    assert {item.code for item in normalize(wrong_zone, asset, mapping, window).rejected} == {
        "temporal_convention_unverified",
    }


def test_futures_normalization_keeps_wti_negative_null_zero_and_missing_close():
    window = DateRange(date(2020, 4, 20), date(2020, 4, 23))
    fields = {"Open": -10, "High": 3, "Low": -40, "Close": -37.63, "Adj Close": -38}
    rows = [
        RawRow(1587355200, {**fields, "Volume": None}),
        RawRow(_new_york_open(date(2020, 4, 21)), {**fields, "Volume": 0}),
        RawRow(_new_york_open(date(2020, 4, 22)), {"Adj Close": 11}),
    ]
    batch, _, mapping = _futures_batch("wti_future", rows, window)
    result = normalize(batch, ASSETS[mapping.asset_id], mapping, window)
    assert [bar.close for bar in result.bars] == [Decimal("-37.63")] * 2
    assert [bar.adjusted_close for bar in result.bars] == [Decimal("-38")] * 2
    assert [bar.volume for bar in result.bars] == [None, Decimal(0)]
    assert [(item.position, item.code) for item in result.rejected] == [(2, "missing_close")]
