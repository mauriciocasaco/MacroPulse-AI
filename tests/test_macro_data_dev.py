import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend.macro_data import dev


def test_dev_apply_requires_explicit_project(monkeypatch):
    def unexpected_connection():
        pytest.fail("credential lookup must follow confirmation")

    monkeypatch.setattr(dev, "_dsn", unexpected_connection)
    with pytest.raises(SystemExit) as error:
        dev.main(["--apply", "--confirm-dev-write", "another-project"])
    assert error.value.code == 2


def test_dev_requires_reviewed_checksum_before_credential_lookup(monkeypatch, capsys):
    def unexpected_connection():
        pytest.fail("credential lookup must follow checksum verification")

    monkeypatch.setattr(dev, "_dsn", unexpected_connection)
    monkeypatch.setattr(dev, "migration_source", lambda: ("reviewed SQL", "a" * 64))
    assert dev.main(["--apply", "--confirm-dev-write", dev.DEV_PROJECT_REF]) == 1
    assert "reviewed migration SHA-256" in capsys.readouterr().out


def test_preflight_errors_do_not_include_connection_details(monkeypatch, capsys):
    def bad_connection():
        raise psycopg.OperationalError("synthetic-secret-sentinel")

    monkeypatch.setattr(dev, "_dsn", bad_connection)
    monkeypatch.setattr(dev, "migration_source", lambda: ("reviewed SQL", "a" * 64))
    assert dev.main(["--preflight"]) == 1
    report = capsys.readouterr().out
    assert "OperationalError" in report
    assert "synthetic-secret-sentinel" not in report


def test_dev_migration_state_and_checksum(disposable_database, monkeypatch):
    source, checksum = dev.migration_source()
    with disposable_database("MACRO_DATA_TEST_DSN") as dsn:
        monkeypatch.setattr(dev, "_DATABASE", conninfo_to_dict(dsn)["dbname"])
        monkeypatch.setattr(dev, "connect_dev", psycopg.connect)
        assert dev.preflight(dsn, checksum) == "pending"
        with psycopg.connect(dsn) as connection:
            assert dev.apply_migration(connection, source, checksum) == "applied"
            assert dev.apply_migration(connection, source, checksum) == "already applied"
            with pytest.raises(dev.MacroDevError, match="reviewed checksum"):
                dev.apply_migration(connection, source + "\n", checksum)
        assert dev.preflight(dsn, checksum) == "ready"
        with pytest.raises(dev.MacroDevError, match="migration state"):
            dev.preflight(dsn, "0" * 64)
        with psycopg.connect(dsn) as connection:
            connection.execute("ALTER TABLE macro_data.capture DISABLE ROW LEVEL SECURITY")
        with pytest.raises(dev.MacroDevError, match="RLS"):
            dev.preflight(dsn, checksum)


def test_preflight_rejects_untracked_existing_schema(disposable_database, monkeypatch):
    _, checksum = dev.migration_source()
    with disposable_database("MACRO_DATA_TEST_DSN") as dsn:
        monkeypatch.setattr(dev, "_DATABASE", conninfo_to_dict(dsn)["dbname"])
        monkeypatch.setattr(dev, "connect_dev", psycopg.connect)
        with psycopg.connect(dsn) as connection:
            connection.execute("CREATE SCHEMA macro_data")
        with pytest.raises(dev.MacroDevError, match="unregistered macro_data"):
            dev.preflight(dsn, checksum)
