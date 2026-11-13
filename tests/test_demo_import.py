"""Synthetic provider captures, offline validation and transactional DEMO import."""

import csv
from dataclasses import replace
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import StringIO
import json
from time import time

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest

from backend import public_demo as demo
from backend.macro_data import demo_import as importer, ecb, eurostat
from backend.macro_data.api import MacroWindow
from backend.macro_data.contracts import CATALOG, format_period
from backend.macro_data.public import read_history, stamp_projection, verify_projection_integrity
from backend.macro_data.transport import Response
from backend.market_data.dev_import import DEV_PROJECT_REF


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


def test_reviewed_digest_rejects_resigned_bundle(captured):
    _, approved, _ = importer.read_bundle(captured)
    source = captured / "eur-usd.response"
    manifest_path = captured / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    updated = source.read_bytes().replace(b",1.0,A,", b",8.0,A,", 1)
    assert updated != source.read_bytes()
    source.write_bytes(updated)
    manifest["captures"]["eur-usd"]["sha256"] = sha256(updated).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert importer.read_bundle(captured)[1] != approved
    with pytest.raises(ValueError, match="reviewed SHA-256"):
        importer.read_bundle(captured, expected_sha256=approved)


def test_duplicate_manifest_key_rejected(captured):
    path = captured / "manifest.json"
    raw = path.read_bytes().replace(b'"start":', b'"start": "2019-12-01", "start":', 1)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Duplicate key"):
        importer.read_bundle(captured)


@pytest.mark.parametrize("pooler", [False, True])
def test_import_apply_pins_shared_target_tls_and_manifest(captured, monkeypatch, tmp_path, pooler):
    _, approved, _ = importer.read_bundle(captured)
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("path fixture")
    password = sha256(b"synthetic administrator fixture").hexdigest()
    host, user = (
        ("synthetic.pooler.supabase.com", f"postgres.{DEV_PROJECT_REF}")
        if pooler else (f"db.{DEV_PROJECT_REF}.supabase.co", "postgres")
    )
    dsn = make_conninfo(host=host, port=5432, dbname="postgres", user=user, password=password)
    connection = object()

    class SyntheticConnection:
        def __enter__(self):
            return connection

        def __exit__(self, *_args):
            pass

    def connect(configured):
        options = conninfo_to_dict(configured)
        assert options["host"] == host and options["user"] == user
        assert options["sslmode"] == "verify-full" and options["sslrootcert"] == str(ca)
        assert options["gssencmode"] == "disable"
        return SyntheticConnection()

    def apply(db, manifest, digest, rows, *, restore_from, expected_target):
        assert db is connection and manifest.start == START and digest == approved
        assert len(rows) == 5 and restore_from is None
        assert expected_target == ("postgres", "postgres")
        return True

    monkeypatch.setattr(importer.os, "environ", {
        "MARKETPULSE_DEMO_PROJECT_REF": DEV_PROJECT_REF,
        "MARKETPULSE_DEMO_ADMIN_DSN": dsn,
        "MARKETPULSE_DEMO_SSL_ROOT_CERT": str(ca),
    })
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(importer, "apply_bundle", apply)
    argv = ["demo_import", "import", str(captured), "--apply", "--project-ref", DEV_PROJECT_REF]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as missing_pin:
        importer.main()
    assert missing_pin.value.code == 2
    argv.extend(["--expected-sha256", approved])
    assert importer.main() == 0
    monkeypatch.setattr(psycopg, "connect", lambda *_: pytest.fail("Invalid target reached PostgreSQL"))
    for bad in (
        make_conninfo(dsn, host="untrusted.example"), make_conninfo(dsn, user="marketpulse_demo_ro"),
        make_conninfo(dsn, port=6543), make_conninfo(dsn, hostaddr="127.0.0.1"),
    ):
        monkeypatch.setitem(importer.os.environ, "MARKETPULSE_DEMO_ADMIN_DSN", bad)
        assert importer.main() == 1
    monkeypatch.setitem(importer.os.environ, "MARKETPULSE_DEMO_ADMIN_DSN", dsn)
    monkeypatch.setitem(importer.os.environ, "PGHOSTADDR", "127.0.0.1")
    assert importer.main() == 1


def test_projection_refuses_a_capture_missing_later_event_windows(tmp_path):
    with pytest.raises(ValueError, match="all public research event windows"):
        importer.capture_bundle(tmp_path / "bundle", START, date(2020, 12, 31),
                                transport=lambda *_: pytest.fail("Invalid window reached transport"))


def test_snapshot_import_idempotence_stale_guard_and_rollback(
    captured, shared_demo_database, install_demo, monkeypatch,
):
    manifest_path = captured / "manifest.json"
    raw_manifest = manifest_path.read_bytes()
    manifest, digest, rows = importer.read_bundle(captured)
    older = json.loads(raw_manifest)
    for entry in older["captures"].values():
        entry["captured_at"] = (datetime.fromisoformat(entry["captured_at"]) - timedelta(hours=1)).isoformat()
    manifest_path.write_text(json.dumps(older), encoding="utf-8")
    older_manifest, older_digest, older_rows = importer.read_bundle(captured)
    with shared_demo_database() as (dsn, db):
        assert db.pgconn.ssl_in_use and db.info.get_parameters()["sslmode"] == "verify-full"
        with psycopg.connect(make_conninfo(dsn, sslmode="require")) as unverified:
            assert unverified.pgconn.ssl_in_use
            with pytest.raises(ValueError, match="requires TLS verify-full"):
                importer.apply_bundle(unverified, manifest, digest, rows)
        db.execute("CREATE SCHEMA macro_data; CREATE TABLE macro_data.private_capture (value integer)")
        db.execute("INSERT INTO macro_data.private_capture VALUES (9)")
        with pytest.raises(ValueError, match="owner-controlled shared DEMO verifier"):
            importer.apply_bundle(db, manifest, digest, rows)
        install_demo(db)
        with pytest.raises(ValueError, match="effective DEMO administrative identity or database"):
            importer.apply_bundle(db, manifest, digest, rows, expected_target=("postgres", "postgres"))
        with pytest.raises(ValueError, match="effective DEMO administrative identity or database"):
            importer.apply_bundle(db, manifest, digest, rows, expected_target=("marketpulse_demo_ro", db.info.dbname))
        assert db.execute("SELECT count(*) FROM marketpulse_demo.macro_catalog").fetchone() == (0,)
        assert importer.apply_bundle(db, manifest, digest, rows)
        assert not importer.apply_bundle(db, manifest, digest, rows)
        with db.transaction():
            db.execute("SET LOCAL session_replication_role = replica")
            db.execute("""
                INSERT INTO marketpulse_demo.macro_observations
                VALUES ('orphan', '2020-01-01', '2020-01-01', '2020-01-01', NULL, 1, 'normal', 'A')
            """)
            with pytest.raises(ValueError, match="origin trigger mode"):
                importer.apply_bundle(db, manifest, digest, rows)
        db.execute("SELECT marketpulse_demo.assert_isolated()")
        verify_projection_integrity(db)
        assert importer.apply_bundle(db, manifest, digest, rows)
        assert db.execute("SELECT count(*) FROM marketpulse_demo.macro_observations WHERE indicator_id = 'orphan'").fetchone() == (0,)
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
        assert {key: fingerprint for key, _, fingerprint in expected} == importer.projection_hashes(manifest, digest, rows)
        assert not importer.apply_bundle(db, manifest, digest, rows)
        db.execute("UPDATE marketpulse_demo.macro_observations SET value_decimal = 99 WHERE indicator_id = 'hicp-yoy'")
        stamp_projection(db)
        verify_projection_integrity(db)
        assert importer.apply_bundle(db, manifest, digest, rows)
        assert not importer.apply_bundle(db, manifest, digest, rows)
        with pytest.raises(ValueError, match="stale"):
            importer.apply_bundle(db, older_manifest, older_digest, older_rows)
        with pytest.raises(ValueError, match="changed"):
            importer.apply_bundle(db, older_manifest, older_digest, older_rows, restore_from="c" * 64)
        bad_row = older_rows[0].rows[0].model_copy(update={"status_kind": "corrupt"})
        bad_publication = replace(older_rows[0], rows=(bad_row, *older_rows[0].rows[1:]))
        with pytest.raises(psycopg.errors.CheckViolation):
            importer.apply_bundle(db, older_manifest, older_digest, (bad_publication, *older_rows[1:]), restore_from=digest)
        verify_projection_integrity(db)
        assert db.execute("SELECT DISTINCT bundle_sha256 FROM marketpulse_demo.macro_catalog").fetchall() == [(digest,)]
        assert importer.apply_bundle(db, older_manifest, older_digest, older_rows, restore_from=digest)
        assert importer.apply_bundle(db, manifest, digest, rows, restore_from=older_digest)
        assert db.execute("SELECT DISTINCT bundle_sha256 FROM marketpulse_demo.macro_catalog").fetchall() == [(digest,)]
        assert db.execute("SELECT value FROM macro_data.private_capture").fetchone() == (9,)
        db.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        with psycopg.connect(make_conninfo(dsn, user="marketpulse_demo_ro"), row_factory=dict_row) as reader:
            verify_projection_integrity(reader)
            result = read_history(reader, CATALOG["deposit-rate"], MacroWindow(start=START, end=END), max_points=2600)
            assert result.coverage_start == date(2019, 12, 2) and result.coverage_end == END
            assert [row.period_raw for row in result.observations] == ["2019-12-02", "2020-02-10", "2024-06-12"]
        _check_imported_public_app(dsn, manifest, rows, monkeypatch)


def _check_imported_public_app(dsn, manifest, publications, monkeypatch):
    from backend.macro_ai import v2_retrieval, v2_service
    from backend.macro_ai.public_gemini import PublicGemini

    origin_secret = sha256(b"synthetic shared DEMO origin").hexdigest()
    password = sha256(b"synthetic shared DEMO password").hexdigest()
    role_dsn = make_conninfo(dsn, user=demo.RO_ROLE, password=password)
    host = f"db.{demo.SHARED_PROJECT_REF}.supabase.co"
    monkeypatch.setattr(demo.os, "environ", {
        "MARKETPULSE_MODE": "public_demo",
        "MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT": "true",
        "MARKETPULSE_DEMO_PROJECT_REF": demo.SHARED_PROJECT_REF,
        "MARKETPULSE_DEMO_DSN": make_conninfo(
            host=host, port=5432, dbname="postgres", user=demo.RO_ROLE, password=password,
        ),
        "MARKETPULSE_DEMO_SSL_ROOT_CERT": conninfo_to_dict(dsn)["sslrootcert"],
        "MARKETPULSE_DEMO_ORIGIN_SECRET": origin_secret,
        "MARKETPULSE_DEMO_RESEARCH_ENABLED": "true",
        "MARKETPULSE_DEMO_GEMINI_API_KEY": "AIza" + "f" * 35,
    })
    connect = psycopg.connect

    def loopback_only(candidate, **kwargs):
        # Preserve production configuration validation. Only the socket
        # destination of the approved synthetic connection is replaced.
        parsed = conninfo_to_dict(candidate)
        assert parsed["host"] == host and parsed["user"] == demo.RO_ROLE
        assert parsed["sslmode"] == "verify-full"
        return connect(role_dsn, **kwargs)

    question = "How did the war in Ukraine relate to euro-area inflation?"
    gemini_calls = []

    def interpret(_provider, request):
        assert request.question == question
        gemini_calls.append("interpret")
        return {
            "event": "ukraine", "purpose": "causal", "requirements": [{
                "excerpt": question, "indicator": "hicp-yoy", "geography": "euro_area",
                "measure": "annual_inflation", "operation": "trend",
            }],
        }

    def compose(_provider, evidence):
        from backend.macro_ai.v2_models import Draft

        gemini_calls.append("compose")
        return Draft.model_validate({
            "paragraphs": [{"statement_id": row["statement_id"], "style": "direct"} for row in evidence],
        })

    monkeypatch.setattr(demo.psycopg, "connect", loopback_only)
    monkeypatch.setattr(PublicGemini, "interpret", interpret)
    monkeypatch.setattr(PublicGemini, "compose", compose)
    monkeypatch.setattr(v2_service, "read_statistics", lambda *_: pytest.fail("DEV reader used"))
    monkeypatch.setattr(v2_retrieval, "semantic", lambda *_: pytest.fail("Local model loaded"))
    headers = {demo.ORIGIN_HEADER: origin_secret, demo.CLIENT_IP_HEADER: "192.0.2.30"}
    catalog_path = "/macro/indicators"
    with TestClient(demo.create_public_app()) as client:
        assert client.get(catalog_path).status_code == 403
        catalog = client.get(catalog_path, headers=headers)
        assert catalog.status_code == 200 and catalog.headers[demo.RESEARCH_HEADER] == "true"
        assert catalog.json() == [
            demo.indicator_metadata(indicator).model_dump(mode="json") for indicator in CATALOG.values()
        ]
        for publication in publications:
            key = publication.item.id
            response = client.get(f"{catalog_path}/{key}/history?start={START}&end={END}", headers=headers)
            assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
            history = demo.MacroHistory.model_validate(response.json())
            assert history.updated_at == manifest.captures[key].captured_at
            assert history.observations == list(publication.rows)
        params = {
            "question": question, "indicator_id": "hicp-yoy", "event_id": "ukraine",
            "start": "2022-02-01", "end": "2024-02-29", "research_version": "v2",
        }
        research = "/macro/analysis"
        assert client.post(research, json=params).status_code == 403
        assert client.post(research, headers=headers, json=params).status_code == 403
        assert gemini_calls == []
        headers["X-MacroPulse-Research-Expires"] = str(int(time() * 1000) + 60_000)
        result = client.post(research, headers=headers, json=params)
        assert result.status_code == 200 and result.headers["X-MacroPulse-Research-Complete"] == "true"
        payload = result.json()
        assert payload["research_v2"] is True and payload["support"] == "partial"
        assert {row["kind"] for row in payload["evidence"]} == {"series", "document"}
        assert gemini_calls == ["interpret", "compose"]
