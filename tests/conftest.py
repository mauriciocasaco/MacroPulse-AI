from hashlib import sha256
import hmac
import json
from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest


@pytest.fixture(scope="module")
def local_test_dsn():
    """Keep Phase 3 integration fixtures on a literal loopback endpoint."""
    def resolve(variable):
        dsn = os.environ.get(variable)
        if not dsn:
            pytest.skip(f"{variable} is unset")
        try:
            options = conninfo_to_dict(dsn)
        except psycopg.Error:
            pytest.fail(f"{variable} is invalid", pytrace=False)
        host = options.get("host")
        if (host not in ("127.0.0.1", "::1") or options.get("hostaddr", host) != host
                or "service" in options or not options.get("port")):
            pytest.fail(f"{variable} must point to disposable PostgreSQL on loopback", pytrace=False)
        return make_conninfo(dsn, hostaddr=host)

    return resolve


@pytest.fixture(scope="module")
def disposable_database(local_test_dsn):
    @contextmanager
    def create(variable, **options):
        admin_dsn = make_conninfo(local_test_dsn(variable), **options)
        database_name = "phase3_test_" + uuid4().hex[:20]
        identifier = sql.Identifier(database_name)
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(identifier))
            try:
                yield make_conninfo(admin_dsn, dbname=database_name)
            finally:
                admin.execute(sql.SQL("DROP DATABASE {}").format(identifier))

    return create


@pytest.fixture
def shared_demo_database(disposable_database, local_test_dsn):
    """Disposable DEMO database with stock PostgreSQL ACLs and role cleanup."""
    @contextmanager
    def create():
        variable = "MACRO_DATA_TEST_DSN"
        with psycopg.connect(local_test_dsn(variable), autocommit=True) as admin:
            if admin.execute("SELECT to_regrole('marketpulse_demo_ro')").fetchone() != (None,):
                pytest.fail("DEMO integration tests require their own cluster without a reader role")
            try:
                with disposable_database(variable) as dsn:
                    with psycopg.connect(dsn, autocommit=True) as db:
                        yield dsn, db
            finally:
                admin.execute("DROP ROLE IF EXISTS marketpulse_demo_ro")

    return create


@pytest.fixture
def install_demo():
    source = Path(__file__).resolve().parents[1] / "supabase/demo/macro_projection.sql"

    def install(db):
        db.execute(source.read_text(encoding="utf-8"))

    return install


@pytest.fixture
def research_attestation(monkeypatch):
    """Synthetic Worker signer for tests of downstream Research behavior."""
    import httpx
    from backend import public_demo

    kid = "synthetic-origin"
    secret = sha256(b"synthetic origin signature fixture").hexdigest()
    verify_kid = "synthetic-verify"
    verify_secret = sha256(b"synthetic callback fixture").hexdigest()
    environment = {
        "MACROPULSE_RESEARCH_ORIGIN_KEYS": json.dumps([[kid, secret]]),
        "MACROPULSE_RESEARCH_VERIFY_KEYS": json.dumps([[verify_kid, verify_secret]]),
        "MACROPULSE_RESEARCH_VERIFY_ACTIVE_KID": verify_kid,
        "MACROPULSE_RESEARCH_HOSTNAME": "demo.example",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    async def permit(*_):
        return True

    monkeypatch.setattr(public_demo, "consume_grant", permit)

    def headers(payload=None, *, content=None, expires_in=90_000, issued_offset=0, nonce=None,
                research_id="cb31e1d0-41f6-4a82-8436-30f47564c5cb", client_ip="192.0.2.3", scope="public"):
        body = content if content is not None else httpx.Request("POST", "https://example.test", json=payload).content
        now = int(public_demo.time() * 1000)
        timestamp, expiration = str(now + issued_offset), str(now + expires_in)
        nonce = nonce or str(uuid4())
        canonical = "\n".join((
            "macropulse/render-research/v1", kid, "POST", "/macro/analysis", "", "application/json",
            sha256(body).hexdigest(), timestamp, nonce, scope, expiration, research_id, client_ip,
        )).encode("ascii")
        return {
            "Content-Type": "application/json",
            "X-MacroPulse-Origin-Key-Id": kid,
            "X-MacroPulse-Origin-Timestamp": timestamp,
            "X-MacroPulse-Origin-Nonce": nonce,
            "X-MacroPulse-Origin-Scope": scope,
            "X-MacroPulse-Origin-Signature": hmac.digest(bytes.fromhex(secret), canonical, "sha256").hex(),
            "X-MacroPulse-Research-Expires": expiration,
            public_demo.RESEARCH_ID_HEADER: research_id,
            public_demo.CLIENT_IP_HEADER: client_ip,
        }

    def post(client, payload):
        return client.post("/macro/analysis", headers=headers(payload), json=payload)

    return SimpleNamespace(headers=headers, post=post, environment=environment)
