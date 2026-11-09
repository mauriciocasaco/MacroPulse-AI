import os
from pathlib import Path
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from backend import api
from backend.market_data import api_config, connection


REF = api_config.DEV_PROJECT_REF
DIRECT = f"host=db.{REF}.supabase.co port=5432 dbname=postgres user=reader password='fixture ${{LITERAL}}' sslmode=require"
POOLER = f"host=fixture.pooler.supabase.com port=6543 dbname=postgres user=reader.{REF} password=fixture"
LOCAL_SCOPE = {"server": ("127.0.0.1", 8001), "client": ("127.0.0.1", 32100),
               "headers": [(b"host", b"127.0.0.1:5173")]}


@pytest.fixture
def config_root(tmp_path, monkeypatch):
    monkeypatch.setattr(api_config, "_ROOT", tmp_path)
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", "8001"])
    for key in ("MARKET_API_DSN", "DATABASE_URL", "MARKETPULSE_DEV_PROJECT_REF", "MARKETPULSE_DISABLE_LOCAL_DOTENV",
                "PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "UVICORN_FD", "UVICORN_UDS", "UVICORN_ENV_FILE",
                *api_config._RO_OPTIONS):
        monkeypatch.delenv(key, raising=False)
    ca = tmp_path / "synthetic-ca.crt"
    ca.touch()
    monkeypatch.setattr(connection, "_CA_CERTIFICATE", ca)
    return tmp_path


def dotenv(root: Path, *, name: str = ".env", project: str = REF, key: str = "MARKET_API_DSN", dsn: str = DIRECT):
    (root / name).write_text(f"MARKETPULSE_DEV_PROJECT_REF={project}\n{key}={dsn}\n", encoding="utf-8-sig")


def test_local_fallback_pins_dev_tls_without_interpolating_or_exporting(config_root, monkeypatch):
    dotenv(config_root)
    monkeypatch.setenv("LITERAL", "different")
    config = api_config.resolve_market_api(LOCAL_SCOPE)
    options = conninfo_to_dict(config.dsn)
    assert config.dev
    assert options["password"] == "fixture ${LITERAL}"
    assert options["sslmode"] == "verify-full" and options["gssencmode"] == "disable"
    assert Path(options["sslrootcert"]) == config_root / "synthetic-ca.crt"
    assert not any(key in os.environ for key in ("MARKET_API_DSN", "DATABASE_URL", "MARKETPULSE_DEV_PROJECT_REF"))
    assert "fixture" not in repr(config)


def test_specific_dotenv_and_explicit_process_dsn_take_precedence(config_root, monkeypatch):
    dotenv(config_root, project="wrong")
    dotenv(config_root, name=".env.market-api", key="MARKET_API_DSN", dsn=POOLER)
    assert conninfo_to_dict(api_config.resolve_market_api(LOCAL_SCOPE).dsn)["port"] == "6543"
    monkeypatch.setenv("MARKET_API_DSN", "host=127.0.0.1 port=5544 user=explicit")
    assert not api_config.resolve_market_api(None).dev
    monkeypatch.setenv("MARKET_API_DSN", "")
    with pytest.raises(api_config.MarketApiConfigError, match="process_dsn_empty"):
        api_config.resolve_market_api(LOCAL_SCOPE)


def test_database_url_is_never_a_fallback(config_root, monkeypatch):
    dotenv(config_root, key="DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", POOLER)
    with pytest.raises(api_config.MarketApiConfigError, match="dev_dsn_missing"):
        api_config.resolve_market_api(LOCAL_SCOPE)


def split_dotenv(root: Path):
    (root / ".env").write_text(
        f"MARKETPULSE_DEV_PROJECT_REF={REF}\nDATABASE_URL={DIRECT}\n"
        f"USER_RO=marketpulse_api_ro.{REF}\nPASS_USER_RO='fixture @:/?% ${{LITERAL}}'\n"
        "HOST_RO=fixture.pooler.supabase.com\nPORT_RO=5432\nDB_RO=postgres\n"
        "SSL_MODE_RO=verify-full\nSSL_ROOT_CERT_RO=synthetic-ca.crt\n",
        encoding="utf-8",
    )


def test_split_settings_escape_password_and_honor_process_precedence(config_root, monkeypatch):
    split_dotenv(config_root)
    dotenv(config_root, name=".env.market-api", dsn=DIRECT)
    monkeypatch.setenv("PORT_RO", "6543")
    options = conninfo_to_dict(api_config.resolve_market_api(LOCAL_SCOPE).dsn)
    assert options["password"] == "fixture @:/?% ${LITERAL}"
    assert options["port"] == "6543"
    assert options["user"] == f"marketpulse_api_ro.{REF}"
    assert Path(options["sslrootcert"]) == config_root / "synthetic-ca.crt"
    monkeypatch.setenv("MARKET_API_DSN", DIRECT)
    assert conninfo_to_dict(api_config.resolve_market_api(LOCAL_SCOPE).dsn)["user"] == "reader"


@pytest.mark.parametrize(("key", "value", "reason"), [
    ("PASS_USER_RO", "", "dev_ro_settings_incomplete"),
    ("USER_RO", f"postgres.{REF}", "dev_ro_user_or_tls_invalid"),
    ("SSL_MODE_RO", "require", "dev_ro_user_or_tls_invalid"),
    ("SSL_ROOT_CERT_RO", "untrusted.crt", "dev_ro_ca_invalid"),
    ("HOST_RO", "unapproved.invalid", "dev_route_or_tls_config_invalid"),
    ("MARKETPULSE_DEV_PROJECT_REF", "wrong", "dev_project_mismatch"),
])
def test_invalid_split_settings_fail_closed(config_root, monkeypatch, key, value, reason):
    split_dotenv(config_root)
    dotenv(config_root, name=".env.market-api", dsn=DIRECT)
    monkeypatch.setenv(key, value)
    with pytest.raises(api_config.MarketApiConfigError, match=reason):
        api_config.resolve_market_api(LOCAL_SCOPE)


def test_process_project_overrides_dotenv(config_root, monkeypatch):
    dotenv(config_root)
    monkeypatch.setenv("MARKETPULSE_DEV_PROJECT_REF", "wrong")
    with pytest.raises(api_config.MarketApiConfigError, match="dev_project_mismatch"):
        api_config.resolve_market_api(LOCAL_SCOPE)


@pytest.mark.parametrize("scope, option, value", [
    ({**LOCAL_SCOPE, "client": ("192.0.2.1", 53000)}, None, None),
    ({**LOCAL_SCOPE, "server": ("0.0.0.0", 8001)}, None, None),
    ({**LOCAL_SCOPE, "headers": [(b"host", b"external.test")]}, None, None),
    ({**LOCAL_SCOPE, "headers": [*LOCAL_SCOPE["headers"], (b"x-forwarded-for", b"127.0.0.1")]}, None, None),
    (LOCAL_SCOPE, "MARKETPULSE_DISABLE_LOCAL_DOTENV", "1"),
    (LOCAL_SCOPE, "UVICORN_FD", "42"),
])
def test_disabled_or_nonlocal_requests_never_read_dotenv(config_root, monkeypatch, scope, option, value):
    if option:
        monkeypatch.setenv(option, value)
    read = Mock(side_effect=AssertionError("dotenv access is forbidden"))
    monkeypatch.setattr(api_config, "_read_dotenv", read)
    with pytest.raises(api_config.MarketApiConfigError, match="local_dotenv_disabled"):
        api_config.resolve_market_api(scope)
    read.assert_not_called()


def test_loopback_request_does_not_make_a_wildcard_launch_local(config_root, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", [*sys.orig_argv[:5], "0.0.0.0", *sys.orig_argv[6:]])
    with pytest.raises(api_config.MarketApiConfigError, match="local_dotenv_disabled"):
        api_config.resolve_market_api(LOCAL_SCOPE)


@pytest.mark.parametrize("dsn", [
    DIRECT.replace(f"db.{REF}.supabase.co", "db.wrong.supabase.co"),
    POOLER.replace(f"reader.{REF}", "reader.wrong"),
    DIRECT + " hostaddr=192.0.2.1",
    DIRECT.replace("sslmode=require", "sslmode=disable"),
])
def test_local_dotenv_rejects_unapproved_destinations_and_options(config_root, dsn):
    dotenv(config_root, dsn=dsn)
    with pytest.raises(api_config.MarketApiConfigError, match="dev_route_or_tls_config_invalid"):
        api_config.resolve_market_api(LOCAL_SCOPE)


def test_explicit_dev_credential_remains_pinned_and_requires_local_launch(config_root, monkeypatch):
    monkeypatch.setenv("MARKET_API_DSN", DIRECT)
    monkeypatch.setenv("MARKETPULSE_DEV_PROJECT_REF", REF)
    pinned = api_config.resolve_market_api(LOCAL_SCOPE)
    assert pinned.dev
    monkeypatch.setenv("MARKET_API_DSN", pinned.dsn)
    assert api_config.resolve_market_api(LOCAL_SCOPE) == pinned
    with pytest.raises(api_config.MarketApiConfigError, match="local_dev_launch_required"):
        api_config.resolve_market_api(None)
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    with pytest.raises(api_config.MarketApiConfigError, match="dev_libpq_override"):
        api_config.resolve_market_api(LOCAL_SCOPE)


def test_rejected_role_returns_generic_http_response_and_safe_log(config_root, monkeypatch, caplog):
    dotenv(config_root)
    db = Mock()
    db.execute.return_value.fetchone.return_value = (False,)
    monkeypatch.setattr(api_config, "connect_dev", lambda _: db)
    with TestClient(api.app, client=("127.0.0.1", 32100), base_url="http://127.0.0.1:8001") as client:
        assert client.get("/health").status_code == 200
        assert db.execute.call_count == 0
        response = client.get("/assets")
    assert response.status_code == 503
    assert response.json() == {"detail": "Market data unavailable"}
    assert "dev_credential_not_read_only" in caplog.text
    assert not any(secret in caplog.text for secret in ("fixture", "LITERAL", "password=", f"db.{REF}"))
    assert db.read_only is True and db.execute.call_count == 4
    db.close.assert_called_once()


def test_connect_error_cannot_surface_raw_libpq_detail(monkeypatch):
    monkeypatch.setattr(api_config, "connect_dev", Mock(side_effect=psycopg.OperationalError("fixture secret")))
    with pytest.raises(api_config.MarketApiConfigError, match="^dev_verified_connection_failed$"):
        api_config.connect_read_only_dev("unused fixture")
