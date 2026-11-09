from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import gzip
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from backend.market_data.backfill import AssetBackfill, export_normalized, write_raw
from backend.market_data import connection as connection_config, dev_import, historical_import, migrations
from backend.market_data.catalog import ASSET_SOURCES
from backend.market_data.historical_import import (
    ImportValidationError, ImportWriteError, _local_dsn, main, persist_import, prepare_import,
)
from backend.market_data.migrations import apply_migrations
from backend.market_data.models import DateRange, RawBatch, RawRow
from backend.market_data.repository import PostgresPriceRepository


@pytest.fixture
def news_migration_db(disposable_database, monkeypatch):
    with disposable_database("MARKETPULSE_NEWS_LOCAL_DSN") as dsn:
        apply_migrations(dsn)
        with psycopg.connect(dsn) as connection:
            connection.execute("CREATE SCHEMA extensions")
            connection.execute("CREATE EXTENSION vector WITH SCHEMA extensions")
        monkeypatch.setattr(dev_import, "connect_dev", psycopg.connect)
        yield dsn


def test_guarded_news_batch_preserves_price_import_and_ledger(news_migration_db, tmp_path):
    dsn = news_migration_db
    path, _ = _archive(tmp_path, "bitcoin_usd")
    exports = prepare_import(path.parent, tmp_path)
    list(persist_import(exports, PostgresPriceRepository(dsn), write=True))
    prices_before = dev_import.preflight_dev(dsn, exports, require_complete=True)
    assert prices_before.versions == (dev_import.INITIAL_VERSION, dev_import.INCREMENTAL_VERSION)
    assert prices_before.bars_by_symbol == {"BTC-USD": 1}
    news_before = dev_import.preflight_dev_news(dsn)
    assert news_before.pending == migrations.NEWS_VERSIONS
    assert not apply_migrations(dsn).applied_versions

    def snapshot():
        with psycopg.connect(dsn) as connection:
            return {
                name: connection.execute(psycopg.sql.SQL(
                    "SELECT to_jsonb(t) FROM market_data.{} t ORDER BY to_jsonb(t)"
                ).format(psycopg.sql.Identifier(name))).fetchall()
                for name in dev_import._SCHEMA_SIGNATURES
            }

    before = snapshot()
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT to_regnamespace('news')").fetchone() == (None,)
        original = connection.execute("SELECT * FROM market_data.schema_migrations ORDER BY version").fetchall()
    assert dev_import.migrate_dev_news(dsn, confirm_project=dev_import.DEV_PROJECT_REF) == migrations.NEWS_VERSIONS
    assert snapshot() == before

    prices_after = dev_import.preflight_dev(dsn, exports, require_complete=True)
    assert prices_after.versions == prices_before.versions + migrations.NEWS_VERSIONS
    assert prices_after.bars_by_symbol == prices_before.bars_by_symbol
    assert prices_after.runs_by_status == prices_before.runs_by_status
    assert dev_import.preflight_dev_news(dsn).pending == ()
    with psycopg.connect(dsn) as connection:
        ledger = connection.execute("SELECT * FROM market_data.schema_migrations ORDER BY version").fetchall()
        assert ledger[:2] == original
        assert {version: checksum for version, checksum, _ in ledger} == migrations._APPROVED_CHECKSUMS
        assert connection.execute(
            "SELECT extensions.vector_norm('[1,0]'::extensions.vector)"
        ).fetchone() == (1.0,)
    assert not apply_migrations(dsn).applied_versions
    with pytest.raises(dev_import.DevImportError, match="requires all three versions to be pending"):
        dev_import.migrate_dev_news(dsn, confirm_project=dev_import.DEV_PROJECT_REF)
    with pytest.raises(RuntimeError, match="unexpected pending migrations"):
        apply_migrations(dsn, expected_pending=migrations.NEWS_VERSIONS)


@pytest.mark.parametrize(("change", "message"), [
    ("INSERT INTO market_data.schema_migrations VALUES ('202611080005', repeat('0', 64))", "unknown migration"),
    ("UPDATE market_data.schema_migrations SET checksum_sha256 = repeat('0', 64) "
     "WHERE version = '202611080004'", "checksum changed"),
    ("DELETE FROM market_data.schema_migrations WHERE version = '202611070001'", "ordered prefix"),
    ("DELETE FROM market_data.schema_migrations WHERE version = '202611080003'", "ordered prefix"),
])
def test_guarded_news_rejects_corrupt_ledger(news_migration_db, change, message):
    dsn = news_migration_db
    apply_migrations(dsn, expected_pending=migrations.NEWS_VERSIONS)
    with psycopg.connect(dsn) as connection:
        connection.execute(change)
    with pytest.raises(RuntimeError, match=message):
        apply_migrations(dsn)
    with pytest.raises(dev_import.DevImportError, match="required migration versions or checksums"):
        dev_import.preflight_dev_news(dsn)


def test_guarded_news_rolls_back_after_004_starts(news_migration_db):
    dsn = news_migration_db
    with psycopg.connect(dsn) as connection:
        connection.execute("""
            CREATE FUNCTION public.reject_vector_ddl() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF to_regclass('news.ingestion_batches') IS NOT NULL THEN
                    RAISE EXCEPTION 'failure in 004 after 003';
                END IF;
            END
            $$;
            CREATE EVENT TRIGGER reject_vector_ddl ON ddl_command_start
            WHEN TAG IN ('CREATE EXTENSION') EXECUTE FUNCTION public.reject_vector_ddl();
        """)
    with pytest.raises(psycopg.errors.RaiseException, match="failure in 004 after 003"):
        apply_migrations(dsn, expected_pending=migrations.NEWS_VERSIONS)
    assert dev_import.preflight_dev_news(dsn).pending == migrations.NEWS_VERSIONS
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT to_regnamespace('news')").fetchone() == (None,)
        assert connection.execute("SELECT count(*) FROM market_data.schema_migrations").fetchone() == (2,)


def test_guarded_news_requires_vector_in_extensions(news_migration_db):
    dsn = news_migration_db
    with psycopg.connect(dsn) as connection:
        connection.execute("ALTER EXTENSION vector SET SCHEMA public")
    with pytest.raises(dev_import.DevImportError, match="pgvector already installed in extensions"):
        dev_import.preflight_dev_news(dsn)
    with pytest.raises(RuntimeError, match="pgvector already installed in extensions"):
        apply_migrations(dsn, expected_pending=migrations.NEWS_VERSIONS)
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT to_regnamespace('news')").fetchone() == (None,)


def test_price_preflight_accepts_valid_prefix_but_news_requires_whole_batch(news_migration_db, tmp_path):
    dsn = news_migration_db
    version = migrations.NEWS_VERSIONS[0]
    source, checksum = migrations._migration_sources()[version]
    with psycopg.connect(dsn) as connection:
        connection.execute(source)
        connection.execute(
            "INSERT INTO market_data.schema_migrations (version, checksum_sha256) VALUES (%s, %s)",
            (version, checksum),
        )
    path, _ = _archive(tmp_path, "bitcoin_usd")
    exports = prepare_import(path.parent, tmp_path)
    assert dev_import.preflight_dev(dsn, exports).versions[-1] == version
    with pytest.raises(dev_import.DevImportError, match="partial batch"):
        dev_import.preflight_dev_news(dsn)
    with pytest.raises(RuntimeError, match="unexpected pending migrations"):
        apply_migrations(dsn, expected_pending=migrations.NEWS_VERSIONS)


def test_guarded_news_requires_project_confirmation(monkeypatch):
    monkeypatch.setattr(dev_import, "configured_dev_dsn", lambda: pytest.fail("opened configuration"))
    with pytest.raises(SystemExit) as error:
        dev_import.main(["--apply-news"])
    assert error.value.code == 2


DAY = timedelta(days=1)
START = date(2020, 4, 20)
WINDOW = DateRange(START, START + 2 * DAY)
RETRIEVED = datetime(2020, 4, 23, 12, 34, 56, 123456, tzinfo=timezone.utc)


def _archive(root, asset_id, *, contradictory=False, duplicate=False):
    mapping = ASSET_SOURCES[asset_id]
    bitcoin = asset_id == "bitcoin_usd"
    zone, label_time = ("UTC", time.min) if bitcoin else ("America/New_York", time(4))
    label = int(datetime.combine(START, label_time, timezone.utc).timestamp())
    close = 10 if bitcoin else -37.63
    prices = {"Open": close, "High": close + 2, "Low": close - 2, "Close": close, "Volume": None}
    if bitcoin:
        prices["Adj Close"] = 9
    rows = [RawRow(label, prices), RawRow(label + int(DAY.total_seconds()), {"Close": None})]
    if contradictory or duplicate:
        extra = {**prices, "Close": close + 1} if contradictory else prices
        rows.append(RawRow(label, extra))
    raw = RawBatch(
        asset_id, mapping, mapping.source_id, mapping.symbol, WINDOW, "1d", RETRIEVED, "1.0.0",
        tuple(rows), {"index_timezone": zone, "out_of_range_timestamps": []}, provider_version="1.8.0",
    )
    path = root / asset_id / f"{WINDOW.start}_{WINDOW.end_exclusive}.json.gz"
    write_raw(path, raw)
    return export_normalized(AssetBackfill(mapping, {path: raw}), root, WINDOW), path


def _change_export(path, keys, value):
    payload = json.loads(gzip.decompress(path.read_bytes()))
    container = payload
    for key in keys[:-1]:
        container = container[key]
    if value is ...:
        del container[keys[-1]]
    else:
        container[keys[-1]] = value
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))


def test_prepare_preserves_decimal_null_window_and_rejection(tmp_path):
    path, archive = _archive(tmp_path, "wti_future")
    before = (path.read_bytes(), archive.read_bytes())
    (export,) = prepare_import(path.parent, tmp_path)
    (prepared,) = export.batches
    (bar,) = prepared.batch.bars
    assert export.window == prepared.window == WINDOW
    assert (bar.bar_date, bar.close, bar.volume, bar.adjusted_close) == (START, Decimal("-37.63"), None, None)
    assert (bar.retrieved_at, bar.rolling_method, bar.provider_version) == (RETRIEVED, "unknown", "1.8.0")
    assert prepared.batch.provenance["requested_end_exclusive"] == WINDOW.end_exclusive
    assert prepared.batch.provenance["raw_sha256"] == prepared.raw_sha256
    assert prepared.batch.received_count == 2
    (rejection,) = prepared.rejected
    assert (rejection.position, rejection.local_date, rejection.code) == (1, START + DAY, "missing_close")
    assert (path.read_bytes(), archive.read_bytes()) == before


@pytest.mark.parametrize(("keys", "value", "message"), [
    (("format_version",), 99, "unsupported normalized format version"),
    (("mapping", "symbol"), "BAD", "export mapping does not match"),
    (("batches", 0, "raw_file"), "../unexpected.json.gz", "unexpected raw archive path"),
    (("batches", 0, "raw_sha256"), "0" * 64, "SHA-256 mismatch"),
    (("batches", 0, "accepted", 0, "bar", "interval"), "1h", "differs from"),
    (("batches", 0, "accepted", 0, "bar", "bar_date"), "2020-04-31", "differs from"),
    (("batches", 0, "accepted", 0, "bar", "close"), "15", "differs from"),
    (("batches", 0, "provenance", "retrieved_at"), "2020-04-22T00:00:00Z", "differs from"),
    (("batches", 0, "rejected"), [], "differs from"),
    (("batches", 0, "accepted"), [], "differs from"),
    (("batches", 0, "provenance"), ..., "invalid archive"),
    (("batches",), [], "export has no batches"),
])
def test_invalid_export_fails_before_writes(tmp_path, keys, value, message):
    path, _ = _archive(tmp_path, "bitcoin_usd")
    _change_export(path, keys, value)
    with pytest.raises(ImportValidationError, match=message):
        prepare_import(path.parent, tmp_path)


def test_corrupt_normalized_gzip_is_rejected(tmp_path):
    path, _ = _archive(tmp_path, "bitcoin_usd")
    path.write_bytes(path.read_bytes()[:-8])
    with pytest.raises(ImportValidationError, match="EOFError"):
        prepare_import(path.parent, tmp_path)


def test_contradictory_duplicates_from_raw_are_rejected(tmp_path):
    path, _ = _archive(tmp_path, "bitcoin_usd", contradictory=True)
    with pytest.raises(ImportValidationError, match="contradictory duplicate bar date"):
        prepare_import(path.parent, tmp_path)


def test_identical_duplicates_preserve_row_accounting(tmp_path):
    path, _ = _archive(tmp_path, "bitcoin_usd", duplicate=True)
    (export,) = prepare_import(path.parent, tmp_path)
    (prepared,) = export.batches
    assert len(prepared.batch.bars) == 2
    assert prepared.batch.received_count == 3


def test_default_dry_run_does_not_open_database_or_modify_files(tmp_path, monkeypatch, capsys):
    path, archive = _archive(tmp_path, "bitcoin_usd")
    original = (path.read_bytes(), archive.read_bytes())

    def unexpected_connection(*args, **kwargs):
        pytest.fail("dry-run opened a database connection")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    monkeypatch.setenv("MARKET_DATA_IMPORT_DSN", "ignored-in-dry-run")
    assert main(["--input-dir", str(path.parent), "--archive-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "DRY RUN" in output and "1 accepted  1 rejected" in output
    assert "ignored-in-dry-run" not in output
    assert (path.read_bytes(), archive.read_bytes()) == original


def test_initial_dataset_gate_blocks_incomplete_set_even_with_apply_flag(tmp_path, monkeypatch, capsys):
    path, _ = _archive(tmp_path, "bitcoin_usd")

    def unexpected_connection(*args, **kwargs):
        pytest.fail("invalid input opened a database connection")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    assert main(["--input-dir", str(path.parent), "--archive-dir", str(tmp_path), "--initial-dataset", "--apply-local"]) == 1
    assert "unexpected initial dataset window" in capsys.readouterr().err


def test_corrupt_second_export_blocks_first_write(tmp_path, monkeypatch, capsys):
    path, _ = _archive(tmp_path, "bitcoin_usd")
    second, _ = _archive(tmp_path, "wti_future")
    _change_export(second, ("batches", 0, "raw_sha256"), "0" * 64)

    def unexpected_connection(*args, **kwargs):
        pytest.fail("input validation opened a database connection")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    assert main(["--input-dir", str(path.parent), "--archive-dir", str(tmp_path), "--apply-local"]) == 1
    assert "CL=F: raw archive SHA-256 mismatch" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [
    "host=remote.invalid", "host=127.0.0.1,remote.invalid",
    "host=127.0.0.1 hostaddr=192.0.2.1", "host=127.0.0.1 service=test",
])
def test_write_configuration_rejects_nonlocal_or_indirect_routing(extra):
    with pytest.raises(ImportWriteError, match="single explicit"):
        _local_dsn(f"dbname=test user=test port=5432 {extra}")


def test_write_requires_explicit_authorization(tmp_path):
    path, _ = _archive(tmp_path, "bitcoin_usd")
    prepared = prepare_import(path.parent, tmp_path)
    with pytest.raises(ImportWriteError, match="explicit write authorization"):
        tuple(persist_import(prepared, None))


def test_keyboard_interrupt_finalizes_known_run(tmp_path):
    path, _ = _archive(tmp_path, "bitcoin_usd")
    exports = prepare_import(path.parent, tmp_path)
    failed = []

    class InterruptedRepository:
        def resolve_mapping_id(self, mapping):
            return 1

        def start_run(self, mapping_id, window):
            return 2

        def save_batch(self, run_id, batch):
            raise KeyboardInterrupt

        def fail_run(self, run_id, code):
            failed.append((run_id, code))

    with pytest.raises(KeyboardInterrupt):
        tuple(persist_import(exports, InterruptedRepository(), write=True))
    assert failed == [(2, "import_interrupted")]


@pytest.mark.skipif(not os.environ.get("MARKET_DATA_TEST_DSN"), reason="MARKET_DATA_TEST_DSN is unset")
def test_postgres_batch_failure_and_idempotent_recovery(tmp_path):
    dsn = os.environ["MARKET_DATA_TEST_DSN"]
    schema = "mdt_" + uuid4().hex[:20]
    apply_migrations(dsn, schema=schema)
    path, _ = _archive(tmp_path, "bitcoin_usd")
    _archive(tmp_path, "wti_future")
    exports = prepare_import(path.parent, tmp_path)
    repository = PostgresPriceRepository(dsn, schema=schema)
    with psycopg.connect(dsn) as connection:
        connection.execute(f"ALTER TABLE {schema}.price_bars ADD CONSTRAINT test_failure CHECK (close > 0)")
    runs = persist_import(exports, repository, write=True)
    first = next(runs)
    assert (first.inserted_count, first.rejected_count) == (1, 1)
    with pytest.raises(ImportWriteError, match="wti_future/.*CheckViolation"):
        next(runs)
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            f"SELECT status, error_code, inserted_count FROM {schema}.ingestion_runs ORDER BY id"
        ).fetchall() == [("partial", None, 1), ("failed", "persistence_failed", 0)]
        connection.execute(f"ALTER TABLE {schema}.price_bars DROP CONSTRAINT test_failure")

    replay = tuple(persist_import(exports, repository, write=True))
    assert [(run.inserted_count, run.unchanged_count, run.rejected_count) for run in replay] == [(0, 1, 1), (1, 0, 1)]
    with psycopg.connect(dsn) as connection:
        bars = connection.execute(
            f"SELECT close, run_id, last_seen_run_id, retrieved_at FROM {schema}.price_bars ORDER BY close DESC"
        ).fetchall()
        diagnostics = connection.execute(
            f"SELECT diagnostics FROM {schema}.ingestion_runs WHERE id = %s", (replay[1].run_id,),
        ).fetchone()[0]
    assert bars == [
        (Decimal("10"), first.run_id, replay[0].run_id, RETRIEVED),
        (Decimal("-37.63"), replay[1].run_id, replay[1].run_id, RETRIEVED),
    ]
    assert "1:missing_close" in diagnostics
    assert any(item.startswith("warning:archive:wti_future/") for item in diagnostics)
    assert any(item.startswith("warning:archive_rejection:1:") for item in diagnostics)


DEV_REF = dev_import.DEV_PROJECT_REF
DEV_HOST = f"db.{DEV_REF}.supabase.co"
POOLER_HOST = "aws-0-us-east-1.pooler.supabase.com"


def _dev_params(host=DEV_HOST, user="postgres", **options):
    return psycopg.conninfo.make_conninfo(
        host=host, port=options.pop("port", "5432"), user=user, dbname="postgres",
        password="synthetic-test-password", **options,
    )


@pytest.mark.parametrize(("ref", "dsn"), [
    ("wrong-project", _dev_params()),
    (DEV_REF, _dev_params("prod.supabase.co")),
    (DEV_REF, _dev_params(f"{DEV_REF}.supabase.co")),
    (DEV_REF, _dev_params(POOLER_HOST, "postgres.other-project")),
    (DEV_REF, _dev_params(POOLER_HOST, "postgres")),
    (DEV_REF, _dev_params(f"{DEV_HOST},localhost")),
    (DEV_REF, _dev_params(hostaddr="127.0.0.1")),
    (DEV_REF, _dev_params(service="ambiguous")),
    (DEV_REF, _dev_params(sslmode="disable")),
    (DEV_REF, _dev_params(sslmode="verify-ca")),
    (DEV_REF, _dev_params(sslrootcert="unapproved.crt")),
])
def test_dev_dsn_rejects_unauthorized_or_ambiguous_configuration(ref, dsn):
    with pytest.raises(dev_import.DevImportError) as caught:
        dev_import.dev_dsn(ref, dsn)
    assert "synthetic-test-password" not in str(caught.value)


@pytest.mark.parametrize(("host", "user", "port"), [
    (DEV_HOST, "postgres", "5432"),
    (POOLER_HOST, f"postgres.{DEV_REF}", "6543"),
])
def test_dev_dsn_pins_tls_and_checks_live_connection(host, user, port, monkeypatch, tmp_path):
    ca = tmp_path / "test-ca.crt"
    ca.touch()
    monkeypatch.setattr(connection_config, "_CA_CERTIFICATE", ca)
    dsn = dev_import.dev_dsn(DEV_REF, _dev_params(host, user, port=port, sslmode="require"))
    params = psycopg.conninfo.conninfo_to_dict(dsn)
    assert params["sslmode"] == "verify-full" and params["gssencmode"] == "disable"
    closed = []
    connection = SimpleNamespace(
        pgconn=SimpleNamespace(ssl_in_use=False),
        info=SimpleNamespace(host=host, port=int(port)),
        close=lambda: closed.append(True),
    )

    def connect(supplied, *, prepare_threshold):
        assert supplied == dsn and prepare_threshold is None
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(dev_import.DevImportError, match="verified TLS"):
        dev_import.connect_dev(dsn)
    assert closed == [True]
    connection.pgconn.ssl_in_use = True
    assert dev_import.connect_dev(dsn) is connection


@pytest.mark.parametrize("args", [
    ["--apply-dev"], ["--apply-dev", "--confirm-dev-write", "other"],
    ["--migrate-dev-incremental"],
    ["--preflight-dev", "--confirm-dev-write", DEV_REF],
    ["--dry-run", "--apply-dev", "--confirm-dev-write", DEV_REF],
])
def test_dev_cli_requires_two_explicit_write_choices(args, monkeypatch):
    monkeypatch.setattr(dev_import, "configured_dev_dsn", lambda: pytest.fail("read DEV credentials"))
    with pytest.raises(SystemExit) as caught:
        main(["--input-dir", "unused", "--archive-dir", "unused", *args])
    assert caught.value.code == 2


@pytest.mark.skipif(not os.environ.get("MARKET_DATA_TEST_DSN"), reason="MARKET_DATA_TEST_DSN is unset")
def test_dev_workflow_on_disposable_postgres(tmp_path, monkeypatch, capsys, disposable_dev_dsn):
    dsn = disposable_dev_dsn
    path, _ = _archive(tmp_path, "bitcoin_usd")
    _archive(tmp_path, "wti_future")
    prepare = historical_import.prepare_import
    exports = prepare(path.parent, tmp_path)

    def synthetic_prepare(input_dir, archive_dir, *, initial_dataset):
        # DEV CLI always requires the real initial-dataset profile. This integration
        # substitutes a smaller synthetic profile after checking that gate was requested.
        assert initial_dataset
        return prepare(input_dir, archive_dir)

    monkeypatch.setattr(historical_import, "prepare_import", synthetic_prepare)
    monkeypatch.setattr(dev_import, "configured_dev_dsn", lambda: dsn)
    monkeypatch.setattr(dev_import, "connect_dev", psycopg.connect)
    args = ["--input-dir", str(path.parent), "--archive-dir", str(tmp_path)]
    confirmation = ["--confirm-dev-write", DEV_REF]
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_MARKET_DATA_MIGRATIONS", migrations._MARKET_DATA_MIGRATIONS[:1])
        assert apply_migrations(dsn).applied_versions == (dev_import.INITIAL_VERSION,)
    assert main([*args, "--apply-dev", *confirmation]) == 1
    assert "required migration versions or checksums" in capsys.readouterr().err
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM market_data.ingestion_runs").fetchone() == (0,)
    assert main([*args, "--migrate-dev-incremental", *confirmation]) == 0
    assert main([*args, "--migrate-dev-incremental", *confirmation]) == 1
    assert "required migration versions or checksums" in capsys.readouterr().err

    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE market_data.asset_sources SET source_convention='unknown' WHERE symbol='CL=F'")
    assert main([*args, "--apply-dev", *confirmation]) == 1
    assert "DEV catalog" in capsys.readouterr().err
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM market_data.ingestion_runs").fetchone() == (0,)
        connection.execute(
            "UPDATE market_data.asset_sources SET source_convention=%s WHERE symbol='CL=F'",
            (ASSET_SOURCES["wti_future"].source_convention,),
        )

    with psycopg.connect(dsn) as connection:
        original = connection.execute(
            "SELECT checksum_sha256 FROM market_data.schema_migrations WHERE version=%s", (dev_import.INCREMENTAL_VERSION,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_data.schema_migrations SET checksum_sha256=%s WHERE version=%s",
            ("0" * 64, dev_import.INCREMENTAL_VERSION),
        )
    assert main([*args, "--preflight-dev"]) == 1
    assert "required migration versions or checksums" in capsys.readouterr().err
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE market_data.schema_migrations SET checksum_sha256=%s WHERE version=%s",
            (original, dev_import.INCREMENTAL_VERSION),
        )
        connection.execute("ALTER TABLE market_data.price_bars ADD CONSTRAINT test_failure CHECK (close > 0)")
    assert main([*args, "--preflight-dev"]) == 1
    assert "incompatible DDL" in capsys.readouterr().err
    with psycopg.connect(dsn) as connection:
        connection.execute("ALTER TABLE market_data.price_bars DROP CONSTRAINT test_failure")
    assert main([*args, "--preflight-dev"]) == 0
    assert main([*args, "--preflight-dev", "--require-complete"]) == 1

    # Introduce a controlled failure after the read-only check, so the first
    # committed batch and failed second run exercise the normal resume path.
    repository = PostgresPriceRepository(dsn)
    with psycopg.connect(dsn) as connection:
        connection.execute("ALTER TABLE market_data.price_bars ADD CONSTRAINT test_failure CHECK (close > 0)")
    with pytest.raises(ImportWriteError, match="CheckViolation"):
        tuple(persist_import(exports, repository, write=True))
    with psycopg.connect(dsn) as connection:
        connection.execute("ALTER TABLE market_data.price_bars DROP CONSTRAINT test_failure")
    capsys.readouterr()
    assert main([*args, "--apply-dev", *confirmation]) == 0
    recovered = capsys.readouterr().out
    assert "inspect run=2 status=failed" in recovered
    assert "inserted=0 unchanged=1" in recovered and "inserted=1 unchanged=0" in recovered
    assert main([*args, "--apply-dev", *confirmation]) == 0
    replayed = capsys.readouterr().out
    assert replayed.count("inserted=0 unchanged=1") == 2
    complete = dev_import.preflight_dev(dsn, exports, require_complete=True)
    assert complete.bars_by_symbol == {"BTC-USD": 1, "CL=F": 1}
    assert complete.runs_by_status == {"failed": 1, "partial": 5}
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE market_data.price_bars SET adjusted_close=8 WHERE close=10")
    assert main([*args, "--apply-dev", *confirmation]) == 1
    assert "unexpected overlap or provenance" in capsys.readouterr().err
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM market_data.ingestion_runs").fetchone() == (6,)


@pytest.fixture
def disposable_dev_dsn():
    dsn = os.environ["MARKET_DATA_TEST_DSN"]
    params = psycopg.conninfo.conninfo_to_dict(dsn)
    assert params.get("host") in ("127.0.0.1", "localhost")
    assert params.get("hostaddr", "127.0.0.1") == "127.0.0.1"
    assert not params.get("service") and not os.environ.get("PGSERVICE") and not os.environ.get("PGHOSTADDR")
    dbname = "mdt_dev_" + uuid4().hex[:20]
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(dbname)))
        try:
            yield psycopg.conninfo.make_conninfo(dsn, dbname=dbname)
        finally:
            connection.execute(psycopg.sql.SQL("DROP DATABASE {}").format(psycopg.sql.Identifier(dbname)))
