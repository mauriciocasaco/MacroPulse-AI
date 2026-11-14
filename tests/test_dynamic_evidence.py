from datetime import date, datetime, timezone

from backend.macro_ai.online_evidence import retrieve_online
from backend.macro_data.api import MacroWindow


def test_dynamic_evidence_preserves_versioned_provenance(disposable_database):
    import psycopg

    with disposable_database("MACRO_DATA_TEST_DSN") as dsn, psycopg.connect(dsn) as owner:
        owner.execute("CREATE SCHEMA macropulse_online")
        owner.execute("""
            CREATE TABLE macropulse_online.news_evidence (
                claim_id text, claim_version text, available_at timestamptz,
                event text DEFAULT 'covid', indicator text DEFAULT 'eur-usd',
                period_start date DEFAULT '2020-06-01', period_end date DEFAULT '2020-06-30',
                published_on date DEFAULT '2020-07-01', relation text DEFAULT 'direct',
                evidence_type text DEFAULT 'observation', measure text DEFAULT 'spot_reference_rate',
                unit text DEFAULT 'USD per EUR', geography text DEFAULT 'bilateral',
                source_id text DEFAULT 'institution-fixture', title text DEFAULT 'Reviewed bulletin',
                canonical_url text DEFAULT 'https://example.org/report',
                evidence_url text DEFAULT 'https://example.org/report#figure',
                attribution text DEFAULT 'Fixture attribution', rights_url text DEFAULT 'https://example.org/rights',
                locator text DEFAULT 'Figure 1', qualifiers text DEFAULT 'No causal estimate.',
                event_linkage text DEFAULT 'contemporaneous_non_causal', summary text DEFAULT 'Reviewed factual text.'
            )
        """)
        owner.execute("""
            INSERT INTO macropulse_online.news_evidence (claim_id, claim_version, available_at, period_end)
            SELECT 'fixture-' || i, repeat('a', 64), '2022-01-01Z', date '2020-06-10' + i
            FROM generate_series(1, 6) i
        """)
        owner.execute("""
            INSERT INTO macropulse_online.news_evidence
            SELECT * FROM macropulse_online.news_evidence WHERE claim_id = 'fixture-1'
        """)
        owner.execute("""
            INSERT INTO macropulse_online.news_evidence (claim_id, claim_version, available_at, unit)
            VALUES ('wrong-unit', repeat('b', 64), '2022-01-01Z', 'EUR per USD')
        """)
        owner.commit()
        window = MacroWindow(start=date(2020, 2, 1), end=date(2020, 12, 31))
        args = "EUR/USD during COVID", "covid", "eur-usd", window
        with psycopg.connect(dsn) as reader:
            reader.read_only = True
            assert not retrieve_online(reader, *args, date(2020, 12, 31), dynamic=True)
            results = retrieve_online(reader, *args, date(2022, 1, 1), dynamic=True)
            assert [source.claim_id for source, _ in results] == [f"fixture-{i}" for i in range(1, 6)]
            source, claim = results[0]
            assert source.claim_version == "a" * 64
            assert source.available_at == datetime(2022, 1, 1, tzinfo=timezone.utc)
            assert source.measure == "spot_reference_rate" and source.unit == "USD per EUR"
            assert source.url.endswith("#figure") and source.locator == "Figure 1"
            assert claim.citations == [source.id]
