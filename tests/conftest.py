from contextlib import contextmanager
import os
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
