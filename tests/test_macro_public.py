from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row
import pytest

from backend import public_demo as demo
from backend.macro_data.api import MacroWindow
from backend.macro_data.contracts import CATALOG
from backend.macro_data.public import ProjectionError, read_history, stamp_projection, verify_projection


SECRET = sha256(b"synthetic macro DEMO origin fixture").hexdigest()
HEADERS = {demo.ORIGIN_HEADER: SECRET, demo.CLIENT_IP_HEADER: "192.0.2.30"}
CATALOG_PATH = "/macro/indicators"
HISTORY_PATH = CATALOG_PATH + "/deposit-rate/history"
WINDOW = "?start=2024-02-01&end=2024-02-10"


def _setup_settings(monkeypatch, tmp_path, **overrides):
    ca = tmp_path / "synthetic-demo-ca.pem"
    ca.write_text("Fixture CA path; no connection is opened")
    fixture_password = sha256(b"synthetic macro projection database fixture").hexdigest()
    settings = {
        "MARKETPULSE_DEMO_PROJECT_REF": "abcdefghijklmnopqrst",
        "MARKETPULSE_DEMO_DSN": (
            "host=db.abcdefghijklmnopqrst.supabase.co port=5432 dbname=postgres "
            f"user=marketpulse_demo_ro password={fixture_password}"
        ),
        "MARKETPULSE_DEMO_ORIGIN_SECRET": SECRET,
        "MARKETPULSE_DEMO_SSL_ROOT_CERT": str(ca),
        **overrides,
    }
    monkeypatch.setattr(demo.os, "environ", settings)
    return settings


def test_projection_required_and_config_fails_closed(monkeypatch, tmp_path):
    settings = _setup_settings(monkeypatch, tmp_path)
    flag = "MARKETPULSE_DEMO_MACRO_ENABLED"
    with pytest.raises(demo.DemoConfigurationError, match="requires the macro projection"):
        demo.resolve_demo_settings({**settings, flag: "false"})
    for key in ("MACRO_API_DSN", "DATABASE_URL", "MARKETPULSE_DEV_PROJECT_REF", "PASS_USER_RO", "GEMINI_API_KEY"):
        with pytest.raises(demo.DemoConfigurationError, match="dedicated"):
            demo.resolve_demo_settings({**settings, flag: "true", key: "synthetic not-for-DEMO fixture"})


def test_macro_enabled_startup_requires_verified_projection(monkeypatch, tmp_path):
    _setup_settings(monkeypatch, tmp_path, MARKETPULSE_DEMO_MACRO_ENABLED="true")

    def invalid(_dsn):
        raise ProjectionError("synthetic invalid projection")

    monkeypatch.setattr(demo, "connect_demo", invalid)
    with pytest.raises(demo.DemoConfigurationError, match="startup verification"):
        with TestClient(demo.create_public_app()):
            pass


def test_macro_opt_in_protects_routes_dates_origin_quota_and_methods(monkeypatch, tmp_path):
    _setup_settings(monkeypatch, tmp_path, MARKETPULSE_DEMO_MACRO_ENABLED="true",
                    MARKETPULSE_DEMO_CATALOG_PER_MINUTE="1")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": {
            "captured_at": datetime(2024, 2, 8, tzinfo=timezone.utc),
            "captured_on": date(2024, 2, 8), "today": date(2024, 2, 10),
        }}),
        MagicMock(**{"fetchone.return_value": {"first_day": date(2024, 2, 1), "last_day": date(2024, 2, 8)}}),
        MagicMock(**{"fetchall.return_value": []}),
    ]

    def connect(_dsn):
        return connection

    monkeypatch.setattr(demo, "connect_demo", connect)
    with TestClient(demo.create_public_app()) as client:
        assert client.get(CATALOG_PATH).status_code == 403
        assert client.get(HISTORY_PATH + WINDOW, headers={demo.ORIGIN_HEADER: SECRET}).status_code == 403
        assert client.post(CATALOG_PATH, headers=HEADERS).status_code == 405
        assert client.get(CATALOG_PATH + "?unknown=fixture", headers=HEADERS).status_code == 400
        assert client.get(HISTORY_PATH + WINDOW + "&start=2024-02-01", headers=HEADERS).status_code == 400
        assert client.get(CATALOG_PATH + "/unapproved/history" + WINDOW, headers=HEADERS).status_code == 404
        start = date(2017, 1, 1)
        end = start + timedelta(days=2600)
        assert client.get(HISTORY_PATH + f"?start={start}&end={end}", headers=HEADERS).status_code == 422
        malformed = client.get(HISTORY_PATH + "?start=2024-01-01&end=fixture-input", headers=HEADERS)
        assert malformed.status_code == 422 and malformed.json() == {"detail": "Invalid request parameters"}
        result = client.get(CATALOG_PATH, headers=HEADERS)
        assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
        assert [item["id"] for item in result.json()] == list(CATALOG)
        assert client.get(CATALOG_PATH, headers=HEADERS).status_code == 429
        result = client.get(HISTORY_PATH + WINDOW, headers=HEADERS)
        assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
        assert result.json()["coverage_end"] == "2024-02-08"


def test_projection_sql_exact_catalog_and_restricted_reader(shared_demo_database, install_demo):
    with shared_demo_database() as (dsn, admin), psycopg.connect(dsn, row_factory=dict_row) as db:
        install_demo(admin)
        for item in CATALOG.values():
            coverage = "{[2024-01-01,2024-02-06),[2024-02-10,2024-02-13)}" if item.id == "deposit-rate" else None
            db.execute("""
                INSERT INTO marketpulse_demo.macro_catalog
                  (indicator_id, series_key, captured_at, requested_start, requested_end, response_sha256,
                   bundle_sha256, deposit_coverage, projected_rows, projection_sha256)
                VALUES (%s, %s, '2024-04-01T10:30:00+00:00', '2024-01-01', '2024-04-01', repeat('a', 64),
                        repeat('b', 64), %s, %s, repeat('0', 64))
            """, (item.id, item.series_key, coverage, 2 if item.id in {"deposit-rate", "hicp-yoy"} else 1))
        db.execute("""
            INSERT INTO marketpulse_demo.macro_observations VALUES
            ('eur-usd', '2024-02-01', '2024-02-01', '2024-02-01', NULL, 1.2340, 'normal', 'A'),
            ('m3-stock', '2024-01', '2024-01-01', '2024-01-31', NULL, 12000000, 'provisional', 'P'),
            ('real-gdp-qoq', '2024-Q1', '2024-01-01', '2024-03-31', NULL, 0.4, 'normal', NULL),
            ('hicp-yoy', '2024-01', '2024-01-01', '2024-01-31', NULL, 2.1, 'normal', NULL),
            ('hicp-yoy', '2024-02', '2024-02-01', '2024-02-29', NULL, NULL, 'withheld', NULL),
            ('deposit-rate', '2024-01-03', '2024-01-03', '2024-01-03', '2024-01-03', 2.0, 'normal', 'A'),
            ('deposit-rate', '2024-02-10', '2024-02-10', '2024-02-10', '2024-02-10', 2.25, 'normal', 'A')
        """)
        stamp_projection(db)
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        assert db.execute("SELECT count(*) AS count FROM marketpulse_demo.macro_catalog").fetchone() == {"count": 5}
        assert db.execute("SELECT count(*) AS count FROM marketpulse_demo.macro_observations").fetchone() == {"count": 7}
        db.execute("SET LOCAL TIME ZONE 'Pacific/Auckland'")
        db.execute("SET LOCAL DateStyle TO 'SQL, DMY'")
        verify_projection(db)
        db.execute("SET LOCAL DateStyle TO 'ISO, YMD'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute("DELETE FROM marketpulse_demo.macro_observations WHERE false")
        rate = read_history(
            db, CATALOG["deposit-rate"], MacroWindow(start=date(2024, 2, 1), end=date(2024, 2, 20)), max_points=2600,
        )
        assert rate.anchor.period_raw == "2024-01-03" and rate.coverage_end == date(2024, 2, 5)
        assert [row.period_raw for row in rate.observations] == ["2024-02-10"]
        hicp = read_history(
            db, CATALOG["hicp-yoy"], MacroWindow(start=date(2024, 1, 1), end=date(2024, 2, 29)), max_points=2600,
        )
        assert [(row.period_raw, row.status_kind) for row in hicp.observations] == [
            ("2024-01", "normal"), ("2024-02", "withheld"),
        ]
        assert hicp.observations[1].value is None
        db.execute("RESET ROLE")
        for change in (
            "DELETE FROM marketpulse_demo.macro_observations WHERE indicator_id = 'deposit-rate' "
            "AND period_start = '2024-02-10'",
            "UPDATE marketpulse_demo.macro_observations SET value_decimal = 9 WHERE indicator_id = 'hicp-yoy' "
            "AND period_start = '2024-01-01'",
            "UPDATE marketpulse_demo.macro_catalog SET deposit_coverage = '{[2024-01-01,2024-03-01)}' "
            "WHERE indicator_id = 'deposit-rate'",
        ):
            with pytest.raises(ProjectionError, match="integrity check failed"), db.transaction():
                db.execute(change)
                db.execute("SET LOCAL ROLE marketpulse_demo_ro")
                verify_projection(db)
        db.execute("ALTER TABLE marketpulse_demo.macro_observations ADD COLUMN unexpected text")
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        with pytest.raises(ProjectionError, match="unexpected columns"):
            verify_projection(db)
        db.execute("RESET ROLE")
        db.execute("ALTER TABLE marketpulse_demo.macro_observations DROP COLUMN unexpected")
        db.execute("CREATE TABLE marketpulse_demo.unrelated (value text)")
        db.execute("GRANT SELECT (value) ON marketpulse_demo.unrelated TO marketpulse_demo_ro")
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        with pytest.raises(ProjectionError, match="read scope"):
            verify_projection(db)
        db.execute("RESET ROLE")
        # The Phase 8.1 runtime guard is still used by this checkout.
        db.execute("CREATE SCHEMA macro_data")
        db.execute("SET LOCAL ROLE marketpulse_demo_ro")
        with pytest.raises(ProjectionError, match="unavailable"):
            verify_projection(db)
        db.rollback()
