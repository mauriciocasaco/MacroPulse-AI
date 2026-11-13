from datetime import date
from decimal import Decimal
from pathlib import Path
from time import time

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from backend import public_demo, public_online
from backend.macro_ai import online_evidence, public_gemini
from backend.macro_ai.v2_models import Draft, Intent
from backend.macro_data.api import MacroWindow, load_history
from backend.macro_data.contracts import CATALOG
from backend.macro_data.repository import register_catalog
from test_macro_api import _sample, _store


ROOT = Path(__file__).resolve().parents[1]
SECRET = "a137db4958d5703962cb29e7f604dbeeef11801341cc6d425b78687e423c59a1"
HEADERS = {public_demo.ORIGIN_HEADER: SECRET, public_demo.CLIENT_IP_HEADER: "192.0.2.1"}


@pytest.fixture(scope="module")
def online_db(disposable_database, local_test_dsn):
    variable = "MACRO_DATA_TEST_DSN"
    with psycopg.connect(local_test_dsn(variable), autocommit=True) as cluster:
        assert cluster.execute("SELECT to_regrole('macropulse_online_ro')").fetchone() == (None,)
        roles = ("anon", "authenticated", "service_role")
        for role in roles:
            cluster.execute(f"CREATE ROLE {role}")
        try:
            with disposable_database(variable) as dsn, psycopg.connect(dsn, autocommit=True) as db:
                for name in ("202611070001_market_data.sql", "202611080002_news.sql",
                             "202611090001_macro_data.sql"):
                    db.execute((ROOT / "supabase/migrations" / name).read_text(encoding="utf-8"))
                db.execute("ALTER TABLE news.article_records ADD COLUMN is_synthetic boolean NOT NULL DEFAULT false")
                register_catalog(db)
                for item in CATALOG.values():
                    period = {"daily": "2019-01-02", "effective_date": "2019-01-02",
                              "quarterly": "2019-Q1", "monthly": "2019-01"}[item.frequency]
                    confidentiality = "F" if item.provider == "ecb" else None
                    _store(db, item.id, "2019-01-01", "2019-03-31", "2019-04-01T10:00:00+00:00", (
                        _sample(item, period, "1.12", confidentiality=confidentiality),
                    ))
                _store(db, "eur-usd", "2019-01-01", "2020-04-01", "2020-04-02T10:00:00+00:00", (
                    _sample(CATALOG["eur-usd"], "2019-01-02", "1.25"),
                    _sample(CATALOG["eur-usd"], "2019-01-04", "9.99", confidentiality="C"),
                    _sample(CATALOG["eur-usd"], "2020-03-12", "1.30"),
                    _sample(CATALOG["eur-usd"], "2020-04-01", "1.35"),
                ))
                _store(db, "hicp-yoy", "2020-02-01", "2020-12-31", "2021-02-01T10:00:00+00:00", (
                    _sample(CATALOG["hicp-yoy"], "2020-02", "1.4", confidentiality=None, flag=None),
                    _sample(CATALOG["hicp-yoy"], "2020-12", "-0.2", confidentiality=None, flag=None),
                ))
                db.execute((ROOT / "supabase/migrations/202611120001_macro_online.sql").read_text(encoding="utf-8"))
                db.execute("ALTER ROLE macropulse_online_ro LOGIN")
                yield dsn
        finally:
            cluster.execute("DROP ROLE IF EXISTS macropulse_online_ro")
            for role in roles:
                cluster.execute(f"DROP ROLE {role}")


def read_online(dsn):
    connection = psycopg.connect(make_conninfo(dsn, user=public_online.ROLE))
    connection.read_only = True
    try:
        public_online.verify_online_role(connection)
    except Exception:
        connection.close()
        raise
    return connection


def test_online_settings_pin_supabase_role_and_tls(tmp_path):
    ca = tmp_path / "fixture.pem"
    ca.touch()
    ref = public_demo.SHARED_PROJECT_REF
    env = {
        "MACROPULSE_ONLINE_DSN": make_conninfo(
            host=f"db.{ref}.supabase.co", port=5432, dbname="postgres",
            user=public_online.ROLE, password="fixture-0123456789-abcdefghijk",
        ),
        "MACROPULSE_ONLINE_SSL_ROOT_CERT": str(ca),
        "MARKETPULSE_DEMO_ORIGIN_SECRET": SECRET,
    }
    options = conninfo_to_dict(public_online.resolve_online_settings(env).dsn)
    assert options["sslmode"] == "verify-full"
    assert options["sslrootcert"] == str(ca)
    assert options["user"] == public_online.ROLE
    for changes in ({"user": "postgres"}, {"sslmode": "disable"}, {"host": "127.0.0.1"}):
        unsafe = make_conninfo(env["MACROPULSE_ONLINE_DSN"], **changes)
        with pytest.raises(public_demo.DemoConfigurationError):
            public_online.resolve_online_settings({**env, "MACROPULSE_ONLINE_DSN": unsafe})


def test_online_matches_local_history_and_masks_private_values(online_db):
    window = MacroWindow(start=date(2019, 1, 3), end=date(2026, 11, 10))
    with psycopg.connect(online_db) as local, read_online(online_db) as online:
        assert [row["id"] for row in public_online.read_online_catalog(online)] == list(CATALOG)
        for item in CATALOG.values():
            assert load_history(local, item, window) == load_history(online, item, window, schema=public_online.SCHEMA)
        assert online.execute("""
            SELECT value_decimal, status_kind FROM macropulse_online.observation_revision
            WHERE indicator_id = 'eur-usd' AND period_raw = '2019-01-04'
        """).fetchone() == (None, "withheld")
        assert online.execute("""
            SELECT value_decimal FROM macropulse_online.observation_revision
            WHERE indicator_id = 'eur-usd' AND period_raw = '2019-01-02'
            ORDER BY revision_id DESC LIMIT 1
        """).fetchone()[0] == Decimal("1.25")


def test_reader_rejects_writes_and_grant_drift(online_db):
    with psycopg.connect(make_conninfo(online_db, user=public_online.ROLE), autocommit=True) as reader:
        reader.execute("SET default_transaction_read_only = off")
        for statement in (
            "SELECT response_body FROM macro_data.capture",
            "SELECT * FROM news.article_records",
            "SELECT * FROM macropulse_online.news_review",
            "DELETE FROM macropulse_online.series",
            "CREATE TABLE macropulse_online.test_write (x int)",
            "SET ROLE postgres",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement)
    with psycopg.connect(online_db, autocommit=True) as admin:
        admin.execute("CREATE SCHEMA net; CREATE TABLE net.http_request_queue (headers text)")
        admin.execute("GRANT SELECT (headers) ON net.http_request_queue TO PUBLIC")
        try:
            with pytest.raises(public_demo.DemoConfigurationError):
                read_online(online_db)
        finally:
            admin.execute("DROP TABLE net.http_request_queue; DROP SCHEMA net")


def test_news_review_requires_current_rights_and_publication_cutoff(online_db):
    with psycopg.connect(online_db) as admin:
        admin.execute("INSERT INTO news.sources (id,name) VALUES ('eia','EIA')")
        article = admin.execute("""
            INSERT INTO news.articles (canonical_url,publisher) SELECT canonical_url,'EIA'
            FROM macropulse_online.news_review RETURNING id
        """).fetchone()[0]
        admin.execute("""
            INSERT INTO news.article_records
                (article_id, source_id, source_url, retrieved_at, reported_published_on, provenance_ref,
                 content_kind, content, storage_permission, public_display_allowed, rights_basis)
            VALUES (%s, 'eia', 'https://www.eia.gov/', now(), '2022-03-14', 'fixture', 'full',
                    'Synthetic body must never enter Research', 'full', true, 'fixture')
        """, (article,))
    window = MacroWindow(start=date(2022, 2, 1), end=date(2024, 2, 29))
    args = ("Inflation during the invasion", "ukraine", "hicp-yoy", window)
    with read_online(online_db) as online:
        assert not online_evidence.retrieve_online(online, *args, date(2022, 3, 13))
        ((source, claim),) = online_evidence.retrieve_online(online, *args, date(2022, 3, 14))
        assert claim.citations == [source.id] and source.url.endswith("id=51618")
        assert "Synthetic body" not in claim.text
    with psycopg.connect(online_db) as admin:
        admin.execute("UPDATE news.article_records SET public_display_allowed = false")
    with read_online(online_db) as online:
        assert not online_evidence.retrieve_online(online, *args, window.end)


def test_online_routes_and_research_with_unselected_event(online_db, monkeypatch):
    monkeypatch.setattr(public_online, "resolve_online_settings", lambda _: public_demo.DemoSettings(make_conninfo(online_db, password="synthetic"), SECRET))
    monkeypatch.setattr(public_online, "connect_online", lambda _: read_online(online_db))
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AIza" + "a" * 35)
    question = "Describe the change in EUR/USD during COVID."
    monkeypatch.setattr(public_gemini.PublicGemini, "interpret", lambda self, request: Intent.model_validate({
        "event": "covid", "purpose": "descriptive", "requirements": [{
            "excerpt": question, "indicator": "eur-usd", "geography": "bilateral", "measure": "spot_reference_rate",
            "operation": "trend",
        }],
    }))
    monkeypatch.setattr(public_gemini.PublicGemini, "compose", lambda self, evidence: Draft.model_validate({
        "sentences": [{"evidence_id": item["evidence_id"], "text": item["sentences"][0]} for item in evidence],
    }))
    with TestClient(public_online.create_online_app()) as client:
        assert client.get("/macro/indicators").status_code == 403
        assert client.get("/assets", headers=HEADERS).status_code == 404
        assert client.get("/macro/indicators", headers=HEADERS).headers[public_demo.RESEARCH_HEADER] == "true"
        path = "/macro/indicators/eur-usd/history?start=2019-01-01&end=2026-11-11"
        assert client.get(path, headers=HEADERS).status_code == 200
        reply = client.post("/macro/analysis", headers={
            **HEADERS, "X-MacroPulse-Research-Expires": str(int((time() + 60) * 1000)),
        }, json={"question": question, "start": "2020-02-01", "end": "2020-12-31", "research_version": "v2"})
        assert reply.status_code == 200
        result = reply.json()
        assert result["status"] == "answered" and result["event_id"] == "covid"
        assert {item["kind"] for item in result["evidence"]} == {"document", "series"}
        assert result["calculations"] and "latest available Supabase" in " ".join(result["limitations"])


def test_online_hicp_covid_preset_reads_official_view_and_m7b(online_db, monkeypatch):
    from backend.macro_ai.v2_retrieval import APPROVED_SNAPSHOT

    monkeypatch.setattr(public_online, "resolve_online_settings", lambda _: public_demo.DemoSettings(
        make_conninfo(online_db, password="synthetic"), SECRET,
    ))
    monkeypatch.setattr(public_online, "connect_online", lambda _: read_online(online_db))
    monkeypatch.setenv("MARKETPULSE_DEMO_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("MARKETPULSE_DEMO_GEMINI_API_KEY", "AIza" + "a" * 35)
    monkeypatch.setattr(public_gemini.PublicGemini, "interpret", lambda *_: pytest.fail("Preset sent to Gemini"))
    monkeypatch.setattr(public_gemini.PublicGemini, "compose", lambda self, evidence: Draft.model_validate({
        "sentences": [{"evidence_id": item["evidence_id"], "text": item["sentences"][0]} for item in evidence],
    }))
    with TestClient(public_online.create_online_app()) as client:
        response = client.post("/macro/analysis", headers={
            **HEADERS, "X-MacroPulse-Research-Expires": str(int((time() + 60) * 1000)),
        }, json={
            "question": "How did the COVID-19 pandemic affect Eurozone Inflation?",
            "indicator_id": "hicp-yoy", "event_id": "covid", "start": "2020-02-01",
            "end": "2020-12-31", "research_version": "v2",
        })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "answered" and result["support"] == "partial"
    assert result["corpus_sha256"] == APPROVED_SNAPSHOT
    assert result["evidence"][0]["series_key"] == CATALOG["hicp-yoy"].series_key
    assert "-1.6 percentage points" in result["calculations"][0]["text"]
    assert result["observed_changes"] == [{
        "indicator_id": "hicp-yoy", "value": "-1.60", "unit": "percentage_points", "direction": "down",
        "first_observed_on": "2020-02-01", "last_observed_on": "2020-12-31", "citation": "series:hicp-yoy",
    }]
    assert len(result["interpretation"]) == 2
