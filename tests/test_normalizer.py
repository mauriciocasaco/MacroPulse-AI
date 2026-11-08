from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.market_data import ASSETS, ASSET_SOURCES, DateRange, RawBatch, RawRow, normalize
from backend.market_data.normalizer import NormalizationStructureError


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
