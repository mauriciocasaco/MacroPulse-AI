from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from io import BytesIO, StringIO
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from backend.macro_data import ecb, eurostat, transport
from backend.macro_data.contracts import CATALOG, parse_period
from backend.macro_data.ingest import _local_dsn, run_ingestion
from backend.macro_data.repository import Capture, read_observations, record_capture, register_catalog
from backend.macro_data.transport import FetchError, Response


DAY = date(2024, 2, 29)
MIGRATION = Path("supabase/migrations/202611090001_macro_data.sql")
FX = CATALOG["eur-usd"]
M3 = CATALOG["m3-stock"]
DFR = CATALOG["deposit-rate"]
GDP = CATALOG["real-gdp-qoq"]
HICP = CATALOG["hicp-yoy"]


def _csv(item, rows, *, unit=None):
    source_unit = {"EXR": "USD", "BSI": "EUR", "FM": "PCPA"}[item.dataset]
    columns = ("KEY", *dict(item.dimensions), "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "OBS_CONF", "UNIT", "UNIT_MULT")
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for period, value, status in rows:
        writer.writerow((
            item.series_key, *dict(item.dimensions).values(), period, value, status, "F",
            unit or source_unit, item.multiplier,
        ))
    return Response(output.getvalue().encode(), 200, "text/csv", 1)


def _jsonstat(item, periods, values, statuses=None, *, change_dimension=None):
    dimensions = {name: {"category": {"index": {value: 0}}} for name, value in item.dimensions}
    dimensions["time"] = {"category": {"index": dict(zip(periods, range(len(periods)), strict=True))}}
    if change_dimension:
        name, code = change_dimension
        dimensions[name]["category"]["index"] = {code: 0}
    body = {
        "version": "2.0", "class": "dataset", "source": "ESTAT", "extension": {"id": item.dataset.upper()},
        "id": list(dimensions), "size": [1] * len(item.dimensions) + [len(periods)], "dimension": dimensions,
        "value": values, "status": statuses, "updated": "2024-04-22T11:00:00+02:00",
    }
    return Response(json.dumps(body).encode(), 200, "application/json", 1)


def _history():
    dimensions = "".join(f'<g:Value id="{key}" value="{value}"/>' for key, value in M3.dimensions)
    versions = []
    for month, value, status in (("03", "100", "P"), ("04", "120", "A")):
        versions.append(f"""
        <m:DataSet action="Replace" validFromDate="2024-{month}-15T12:00:00+02:00">
          <g:Series><g:SeriesKey>{dimensions}</g:SeriesKey>
          <g:Attributes><g:Value id="UNIT" value="EUR"/><g:Value id="UNIT_MULT" value="6"/></g:Attributes>
          <g:Obs><g:ObsDimension value="2024-02"/><g:ObsValue value="{value}"/>
          <g:Attributes><g:Value id="OBS_STATUS" value="{status}"/><g:Value id="OBS_CONF" value="F"/></g:Attributes>
          </g:Obs></g:Series>
        </m:DataSet>""")
    xml = f"""
    <m:GenericData xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
      xmlns:g="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic">
      <m:Header><m:Sender id="ECB"/></m:Header>{''.join(versions)}
    </m:GenericData>
    """
    return Response(xml.encode(), 200, "application/vnd.sdmx.genericdata+xml", 1)


@pytest.fixture(scope="module")
def macro_dsn(disposable_database):
    with disposable_database("MACRO_DATA_TEST_DSN") as dsn:
        with psycopg.connect(dsn) as db:
            db.execute(MIGRATION.read_text(encoding="utf-8"))
        yield dsn


def test_contract_values_and_periods():
    assert set(CATALOG) == {"eur-usd", "m3-stock", "deposit-rate", "real-gdp-qoq", "hicp-yoy"}
    assert FX.series_key == "EXR.D.USD.EUR.SP00.A"
    assert M3.series_key == "BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E"
    assert DFR.series_key == "FM.B.U2.EUR.4F.KR.DFR.LEV"
    assert GDP.series_key == "namq_10_gdp?freq=Q&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ&geo=EA21"
    assert HICP.series_key == "prc_hicp_minr?freq=M&unit=RCH_A&coicop18=TOTAL&geo=EA"
    assert "sinceTimePeriod=2024-Q1" in eurostat.request_url(GDP, DAY, DAY)
    assert "startPeriod=2024-02" in ecb.request_url(M3, DAY, DAY)
    assert parse_period("monthly", "2024-02").end == DAY
    assert parse_period("quarterly", "2024-Q4").start == date(2024, 10, 1)
    assert parse_period("quarterly", "2024-Q3").end == date(2024, 9, 30)
    assert parse_period("effective_date", "2024-02-29").effective_date == DAY
    with pytest.raises(ValueError):
        parse_period("monthly", "2024-2")


def test_ecb_rejects_unit_drift_and_preserves_explicit_missing():
    with pytest.raises(ValueError, match="units"):
        ecb.parse(FX, _csv(FX, [(DAY.isoformat(), "2.35", "A")], unit="EUR"))
    observations, _ = ecb.parse(M3, _csv(M3, [("2024-02", "", "M")]))
    assert observations[0].value is None and observations[0].status_kind == "missing"
    assert observations[0].status_raw == "M"
    assert ecb.parse(DFR, Response(b"", 200, "text/csv", 1))[0] == ()
    with pytest.raises(ValueError):
        ecb.parse(M3, Response(b"", 200, "text/csv", 1))


def test_eurostat_sparse_decimal_and_dimension_validation():
    body = _jsonstat(HICP, ["2024-01", "2024-02"], {"0": -1.23}, {"0": "e", "1": "c"})
    observations, attributes = eurostat.parse(HICP, body)
    assert observations[0].value == Decimal("-1.23")
    assert observations[0].status_kind == "estimated"
    assert observations[1].value is None and observations[1].confidentiality_raw == "c"
    assert attributes["dataset_updated"] == "2024-04-22T11:00:00+02:00"
    with pytest.raises(ValueError, match="selection"):
        eurostat.parse(GDP, _jsonstat(GDP, ["2024-Q1"], [0.3], change_dimension=("unit", "CLV_PCH_ANN")))


def test_transport_retries_and_rejects_unofficial_host(monkeypatch):
    class Unreachable:
        def __init__(self):
            self.calls = 0

        def open(self, request, timeout):
            self.calls += 1
            assert timeout == transport.TIMEOUT
            raise TimeoutError

    opener = Unreachable()
    pauses = []
    monkeypatch.setattr(transport, "build_opener", lambda *args: opener)
    monkeypatch.setattr(transport.time, "sleep", pauses.append)
    with pytest.raises(FetchError) as failure:
        transport.fetch("https://data-api.ecb.europa.eu/service/data", ecb.ACCEPT)
    assert failure.value.attempts == 3 and opener.calls == 3 and pauses == [1, 2]
    with pytest.raises(ValueError, match="endpoint"):
        transport.fetch("https://data-api.ecb.europa.eu.example.test/service/data", ecb.ACCEPT)
    with pytest.raises(ValueError, match="disposable"):
        _local_dsn("host=127.0.0.1 port=5432 dbname=ordinary user=synthetic")
    with pytest.raises(ValueError, match="disposable"):
        _local_dsn("host=127.0.0.1 hostaddr=192.0.2.1 port=5432 dbname=macropulse_m2_test user=synthetic")


def test_partial_failures_valid_empty_and_duplicate_rejection(macro_dsn):
    def fake(url, _):
        if "/EXR/" in url:
            return _csv(FX, [(DAY.isoformat(), "2.345", "A"), (DAY.isoformat(), "2.345", "A")])
        if "/BSI/" in url:
            raise FetchError("provider_http_error", 3, 503)
        if "/FM/" in url:
            return Response(b"", 200, "text/csv", 1)
        if "/namq_10_gdp?" in url:
            return _jsonstat(GDP, ["2024-Q1"], [0.4])
        return _jsonstat(HICP, ["2024-02"], [1.4])

    with psycopg.connect(macro_dsn) as db:
        results = run_ingestion(db, DAY, DAY, transport=fake)
        assert [(row.outcome, row.error_code, row.observations) for row in results] == [
            ("failed", "invalid_provider_response", 0), ("failed", "provider_http_error", 0),
            ("success", None, 0), ("success", None, 1), ("success", None, 1),
        ]
        rows = read_observations(db, GDP.id)
        assert rows[0]["value_decimal"] == Decimal("0.4")
        assert rows[0]["period_end"] == date(2024, 3, 31)
        assert rows[0]["provider_valid_from"] is None
        assert db.execute("SELECT count(*) FROM macro_data.series").fetchone()[0] == 5
        assert db.execute("""
            SELECT count(*) FROM macro_data.capture WHERE outcome = 'failed' AND response_body IS NULL
        """).fetchone()[0] == 2


def _prepared_capture(hour, raw, *, indicator=FX, period="2024-02-29"):
    response = _csv(indicator, [(period, raw, "A")])
    items, _ = ecb.parse(indicator, response)
    captured = datetime(2024, 4, 25, hour, tzinfo=timezone.utc)
    capture = Capture(uuid4(), DAY, DAY, captured, ecb.request_url(indicator, DAY, DAY), 200, 1, response.body)
    return capture, items


def test_append_only_a_b_a_replay_cutoff_and_stale(macro_dsn):
    first, second, third, fourth = (
        _prepared_capture(hour, raw) for hour, raw in ((2, "2.34"), (3, "2.34"), (4, "2.37"), (5, "2.34"))
    )
    stale = _prepared_capture(1, "2.33")
    with psycopg.connect(macro_dsn) as db:
        register_catalog(db)
        assert [record_capture(db, FX, *item).new_revisions for item in (first, second, third, fourth)] == [1, 0, 1, 1]
        assert record_capture(db, FX, *first).new_revisions == 1
        assert record_capture(db, FX, *stale).stale_observations == 1
        assert db.execute(
            "SELECT response_body FROM macro_data.capture WHERE capture_id = %s", (stale[0].id,),
        ).fetchone()[0] == stale[0].body
        assert read_observations(db, FX.id, known_at=stale[0].captured_at) == []
        row = read_observations(db, FX.id, known_at=second[0].captured_at)[0]
        assert row["first_seen_at"] == first[0].captured_at and row["last_seen_at"] == second[0].captured_at
        assert read_observations(db, FX.id, known_at=third[0].captured_at)[0]["value_decimal"] == Decimal("2.37")
        assert read_observations(db, FX.id)[0]["value_decimal"] == Decimal("2.34")


def test_concurrent_deduplication_of_one_period(macro_dsn):
    gate = Barrier(2)
    sample = [_prepared_capture(4, "2.35", indicator=DFR) for _ in range(2)]

    def save(prepared):
        with psycopg.connect(macro_dsn) as db:
            gate.wait(timeout=5)
            return record_capture(db, DFR, *prepared)

    with psycopg.connect(macro_dsn) as db:
        register_catalog(db)
    with ThreadPoolExecutor(2) as pool:
        results = tuple(pool.map(save, sample))
    assert sum(row.new_revisions for row in results) == 1
    assert sum(row.stale_observations for row in results) == 0
    with psycopg.connect(macro_dsn) as db:
        rows = read_observations(db, DFR.id)
        assert len(rows) == 1 and rows[0]["effective_date"] == DAY


def test_history_evidence_does_not_replace_local_snapshot(macro_dsn):
    def fake(url, accept):
        assert "includeHistory=true" in url and accept == ecb.HISTORY_ACCEPT
        return _history()

    with psycopg.connect(macro_dsn) as db:
        first = run_ingestion(db, DAY, DAY, ids=(M3.id,), transport=fake, ecb_history=True)
        second = run_ingestion(db, DAY, DAY, ids=(M3.id,), transport=fake, ecb_history=True)
        assert first[0].new_revisions == 2 and second[0].new_revisions == 0
        assert read_observations(db, M3.id) == []
        rows = db.execute("""
            SELECT provider_valid_from, value_decimal, status_kind
            FROM macro_data.observation_revision WHERE indicator_id = %s ORDER BY provider_valid_from
        """, (M3.id,)).fetchall()
        assert [(stamp.month, value, state) for stamp, value, state in rows] == [
            (3, Decimal("100"), "provisional"), (4, Decimal("120"), "normal"),
        ]


def test_body_reset_retries_and_terminal_failure_preserves_other_series(macro_dsn, monkeypatch):
    day = date(2024, 3, 1)
    fx = _csv(FX, [(day.isoformat(), "2.34", "A")])
    m3 = _csv(M3, [("2024-03", "100", "A")])
    hicp = _jsonstat(HICP, ["2024-03"], [1.2])
    calls = {FX.id: 0, M3.id: 0, HICP.id: 0}
    pauses = []

    class Body(BytesIO):
        status = 200

        def __init__(self, reply, reset):
            super().__init__(reply.body)
            self.reset = reset
            self.headers = EmailMessage()
            self.headers["Content-Type"] = reply.content_type
            self.headers["Content-Length"] = str(len(reply.body))

        def read1(self, size):
            if self.reset:
                if self.tell() > 0:
                    raise ConnectionResetError("synthetic body read reset")
                return super().read1(4)
            return super().read1(size)

    class Opener:
        def open(self, request, timeout):
            if "/EXR/" in request.full_url:
                calls[FX.id] += 1
                return Body(fx, reset=calls[FX.id] == 1)
            if "/BSI/" in request.full_url:
                calls[M3.id] += 1
                return Body(m3, reset=True)
            calls[HICP.id] += 1
            return Body(hicp, reset=False)

    monkeypatch.setattr(transport, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(transport.time, "sleep", pauses.append)
    with psycopg.connect(macro_dsn) as db:
        results = run_ingestion(db, day, day, ids=(FX.id, M3.id, HICP.id))
        assert [(row.outcome, row.attempts, row.http_status, row.error_code) for row in results] == [
            ("success", 2, 200, None), ("failed", 3, 200, "provider_transport_error"), ("success", 1, 200, None),
        ]
        assert db.execute("""
            SELECT outcome, response_body, response_sha256
            FROM macro_data.capture WHERE capture_id = %s
        """, (results[1].capture_id,)).fetchone() == ("failed", None, None)
        assert read_observations(db, HICP.id)[-1]["value_decimal"] == Decimal("1.2")
    assert calls == {FX.id: 2, M3.id: 3, HICP.id: 1} and pauses == [1, 1, 2]
