"""Synthetic provider captures, offline validation and transactional DEMO import."""

import csv
from datetime import date, timedelta
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest

from backend.macro_data import demo_import as importer, ecb, eurostat
from backend.macro_data.api import MacroWindow
from backend.macro_data.contracts import CATALOG, format_period
from backend.macro_data.public import read_history, verify_projection
from backend.macro_data.transport import Response


START, END = date(2019, 12, 1), date(2026, 9, 30)


def fixture_periods(frequency):
    days = [START + timedelta(days=offset) for offset in range(0, (END - START).days + 1, 7)]
    return list(dict.fromkeys(format_period(frequency, day) for day in days))


def fixture_response(item, *, bad_unit=False, restricted=False, invalid_date=False, gap=False):
    if item.provider == "ecb":
        periods = (["2019-12-02", "2020-02-10", "2024-06-12"] if item.frequency == "effective_date"
                   else fixture_periods(item.frequency))
        if invalid_date:
            periods = ["2020-02-30"]
        if gap:
            periods = [periods[0], periods[-1]]
        content = StringIO()
        writer = csv.writer(content)
        writer.writerow(["KEY", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "OBS_CONF", "UNIT", "UNIT_MULT",
                         *[key for key, _ in item.dimensions]])
        for index, period in enumerate(periods):
            writer.writerow([item.series_key, period, f"1.{index}", "A", "C" if restricted else "F",
                             "bad" if bad_unit else ecb.UNITS[item.dataset], item.multiplier,
                             *[value for _, value in item.dimensions]])
        return Response(content.getvalue().encode(), 200, "text/csv", 1)
    periods = fixture_periods(item.frequency)
    keys = dict(item.dimensions)
    dimensions = {key: {"category": {"index": [value]}} for key, value in keys.items()}
    dimensions["time"] = {"category": {"index": periods}}
    raw = {"class": "dataset", "version": "2.0", "source": "ESTAT", "extension": {"id": item.dataset},
           "id": [*keys, "time"], "size": [*[1 for _ in keys], len(periods)], "dimension": dimensions,
           "value": [2.1] * len(periods)}
    return Response(json.dumps(raw).encode(), 200, "application/json", 1)


@pytest.fixture
def captured(tmp_path):
    fixture = {
        adapter.request_url(item, START, END): fixture_response(item)
        for item in CATALOG.values()
        for adapter in [ecb if item.provider == "ecb" else eurostat]
    }
    path = tmp_path / "bundle"
    importer.capture_bundle(path, START, END, transport=lambda url, _accept: fixture[url])
    return path


def test_dry_run_checks_five_official_providers_without_database_or_network(captured, monkeypatch, capsys):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Dry run tried to contact an external service")

    monkeypatch.setattr(psycopg, "connect", unexpected)
    monkeypatch.setattr(importer, "fetch", unexpected)
    monkeypatch.setattr("sys.argv", ["demo_import", "import", str(captured)])
    assert importer.main() == 0
    assert "Database untouched" in capsys.readouterr().out
    manifest, digest, rows = importer.read_bundle(captured)
    assert digest == sha256((captured / "manifest.json").read_bytes()).hexdigest()
    assert list(manifest.captures) == list(CATALOG)
    assert [row.item.unit for row in rows] == ["USD/EUR", "EUR", "percent_per_annum", "percent_qoq", "percent_yoy"]
    assert rows[2].coverage[0].lower == date(2019, 12, 2)


@pytest.mark.parametrize("problem", ["checksum", "provider", "units", "restricted", "invalid_date", "gap"])
def test_bundle_rejects_invalid_provenance_units_dates_and_coverage(captured, problem):
    source = captured / "eur-usd.response"
    manifest_path = captured / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if problem == "checksum":
        source.write_bytes(b"fixture tampering")
    elif problem == "provider":
        manifest["captures"]["eur-usd"]["url"] = "https://untrusted.example/"
    else:
        options = {"bad_unit" if problem == "units" else problem: True}
        body = fixture_response(CATALOG["eur-usd"], **options).body
        source.write_bytes(body)
        manifest["captures"]["eur-usd"]["sha256"] = sha256(body).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        importer.read_bundle(captured)


def test_projection_refuses_a_capture_missing_later_event_windows(tmp_path):
    with pytest.raises(ValueError, match="all public research event windows"):
        importer.capture_bundle(tmp_path / "bundle", START, date(2020, 12, 31),
                                transport=lambda *_: pytest.fail("Invalid window reached transport"))


def test_snapshot_import_idempotence_stale_guard_and_rollback(captured, disposable_database, monkeypatch):
    manifest, digest, rows = importer.read_bundle(captured)
    with disposable_database("MACRO_DATA_TEST_DSN") as dsn, psycopg.connect(dsn) as db:
        if not db.pgconn.ssl_in_use:
            pytest.skip("requires TLS disposable PostgreSQL fixture")
        sql = Path("supabase/demo/macro_projection.sql").read_text(encoding="utf-8")
        body = sql.split("BEGIN;\n", 1)[1].rsplit("COMMIT;", 1)[0]
        body = body.replace("current_database() <> 'postgres'", f"current_database() <> '{db.info.dbname}'")
        db.execute(body.replace("DATABASE postgres", f"DATABASE {db.info.dbname}"))
        with pytest.raises(ValueError, match="verified isolated DEMO"):
            importer.apply_bundle(db, manifest, digest, rows)
        monkeypatch.setattr(importer, "_verify_admin", lambda connection: connection is db)
        assert importer.apply_bundle(db, manifest, digest, rows)
        assert not importer.apply_bundle(db, manifest, digest, rows)
        expected = db.execute("""
            SELECT indicator_id, projected_rows, projection_sha256
            FROM marketpulse_demo.macro_catalog ORDER BY indicator_id
        """).fetchall()
        db.execute("""
            DELETE FROM marketpulse_demo.macro_observations
            WHERE indicator_id = 'deposit-rate' AND period_start = '2020-02-10'
        """)
        db.execute("UPDATE marketpulse_demo.macro_observations SET value_decimal = 99 WHERE indicator_id = 'hicp-yoy'")
        with pytest.raises(ValueError, match="changed"):
            importer.apply_bundle(db, manifest, digest, rows, restore_from="c" * 64)
        assert importer.apply_bundle(db, manifest, digest, rows)
        assert db.execute("""
            SELECT indicator_id, projected_rows, projection_sha256
            FROM marketpulse_demo.macro_catalog ORDER BY indicator_id
        """).fetchall() == expected
        assert not importer.apply_bundle(db, manifest, digest, rows)
        with pytest.raises(ValueError, match="stale"):
            importer.apply_bundle(db, manifest, "b" * 64, rows)
        with pytest.raises(ValueError, match="changed"):
            importer.apply_bundle(db, manifest, "b" * 64, rows, restore_from="c" * 64)
        assert importer.apply_bundle(db, manifest, "b" * 64, rows, restore_from=digest)
        assert importer.apply_bundle(db, manifest, digest, rows, restore_from="b" * 64)
        assert db.execute("SELECT DISTINCT bundle_sha256 FROM marketpulse_demo.macro_catalog").fetchall() == [(digest,)]
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        verify_projection(db)
        db.row_factory = dict_row
        result = read_history(db, CATALOG["deposit-rate"], MacroWindow(start=START, end=END), max_points=2600)
        assert result.coverage_start == date(2019, 12, 2) and result.coverage_end == END
        assert [row.period_raw for row in result.observations] == ["2019-12-02", "2020-02-10", "2024-06-12"]
        db.rollback()
