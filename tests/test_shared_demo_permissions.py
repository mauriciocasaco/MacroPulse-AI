"""Adversarial ACL checks using actual PostgreSQL sessions on disposable loopback."""

import psycopg
from psycopg import pq, sql
from psycopg.conninfo import make_conninfo
import pytest


def _prepare_dev(db):
    db.execute("""
        CREATE SCHEMA macro_data;
        CREATE SCHEMA market_data;
        CREATE SCHEMA news;
        CREATE EXTENSION pgcrypto;
        CREATE EXTENSION pg_stat_statements;
        CREATE TABLE macro_data.private_capture (internal_value integer);
        INSERT INTO macro_data.private_capture VALUES (9);
        CREATE TABLE public.private_metrics (metric integer);
        INSERT INTO public.private_metrics VALUES (5);
        CREATE SEQUENCE public.private_seq;
        CREATE FUNCTION macro_data.private_read() RETURNS integer
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
        AS 'SELECT internal_value FROM macro_data.private_capture';
    """)


def test_shared_reader_isolated_even_in_read_write_transaction(shared_demo_database, install_demo):
    with shared_demo_database() as (dsn, db):
        _prepare_dev(db)
        # Preserve unrelated database ACLs, PUBLIC access to normal extension
        # routines, and explicit grants to a separate role on DEV objects.
        db.execute("GRANT USAGE ON SCHEMA macro_data TO pg_monitor")
        db.execute("GRANT SELECT ON macro_data.private_capture TO pg_monitor")
        # This otherwise harmless existing role can be normalized.
        db.execute("CREATE ROLE marketpulse_demo_ro")
        db.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO marketpulse_demo_ro").format(
            sql.Identifier(db.info.dbname)
        ))
        db.execute("GRANT USAGE ON SCHEMA public TO marketpulse_demo_ro")
        before = _dev_acl(db)
        install_demo(db)
        db.execute("""
            GRANT CREATE ON SCHEMA marketpulse_demo TO marketpulse_demo_ro;
            GRANT UPDATE (status_raw), REFERENCES (indicator_id) ON marketpulse_demo.macro_observations
                TO marketpulse_demo_ro;
            GRANT SELECT ON marketpulse_demo.macro_catalog TO marketpulse_demo_ro WITH GRANT OPTION;
            GRANT INSERT (period_raw) ON marketpulse_demo.macro_observations TO PUBLIC;
        """)
        install_demo(db)
        assert db.execute("SELECT rolcanlogin, rolinherit FROM pg_roles WHERE rolname = 'marketpulse_demo_ro'").fetchone() == (
            False, False,
        )
        db.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        reader_dsn = make_conninfo(dsn, user="marketpulse_demo_ro")
        with psycopg.connect(reader_dsn, autocommit=True) as reader:
            assert reader.pgconn.ssl_in_use
            assert reader.execute("""
                SELECT current_user, session_user, current_setting('default_transaction_read_only'),
                    current_setting('search_path')
            """).fetchone() == ("marketpulse_demo_ro", "marketpulse_demo_ro", "on", "pg_catalog")
            with reader.transaction():
                reader.execute("SET TRANSACTION READ WRITE")
                assert reader.execute("SELECT count(*) FROM marketpulse_demo.macro_catalog").fetchone() == (0,)
                assert reader.execute("SELECT count(*) FROM marketpulse_demo.macro_observations").fetchone() == (0,)
                assert reader.execute("""
                    SELECT NOT has_table_privilege('marketpulse_demo.macro_catalog', 'SELECT WITH GRANT OPTION')
                """).fetchone() == (True,)
                reader.execute("CREATE TEMPORARY TABLE session_local (id integer)")
                reader.execute("INSERT INTO session_local VALUES (11)")
                assert reader.execute("SELECT id FROM session_local").fetchone() == (11,)
                assert reader.execute("SELECT public.digest('fixture', 'sha256') IS NOT NULL").fetchone() == (True,)
                (large_object,) = reader.execute("SELECT lo_from_bytea(0, 'synthetic'::bytea)").fetchone()
                assert reader.execute("SELECT lo_get(%s)", (large_object,)).fetchone() == (b"synthetic",)
                db.execute("SELECT marketpulse_demo.assert_isolated()")
                for command in (
                    "SELECT * FROM macro_data.private_capture",
                    "UPDATE macro_data.private_capture SET internal_value = 99",
                    "SELECT * FROM public.private_metrics",
                    "INSERT INTO public.private_metrics VALUES (99)",
                    "SELECT * FROM pg_catalog.pg_authid",
                    "SELECT macro_data.private_read()",
                    "SELECT marketpulse_demo.assert_isolated()",
                    "SELECT nextval('public.private_seq')",
                    "SELECT pg_read_file('postgresql.conf')",
                    "DELETE FROM marketpulse_demo.macro_observations WHERE false",
                    "UPDATE marketpulse_demo.macro_observations SET status_raw = 'A' WHERE false",
                    "INSERT INTO marketpulse_demo.macro_observations (period_raw) VALUES ('2020-01-01')",
                    "TRUNCATE marketpulse_demo.macro_catalog",
                    "CREATE TABLE marketpulse_demo.injected (id integer)",
                    "CREATE TABLE public.injected (id integer)",
                    "CREATE SCHEMA injected",
                    "CREATE ROLE injected",
                    "SET ROLE postgres",
                    "SET ROLE pg_read_all_data",
                ):
                    with pytest.raises(psycopg.errors.InsufficientPrivilege), reader.transaction():
                        reader.execute(command)
        assert _dev_acl(db) == before
        assert db.execute("SELECT internal_value FROM macro_data.private_capture").fetchone() == (9,)
        assert db.execute("SELECT metric FROM public.private_metrics").fetchone() == (5,)
        db.execute("SELECT marketpulse_demo.assert_isolated()")


def _dev_acl(db):
    return db.execute("""
        SELECT
            (SELECT array_agg(a::text ORDER BY a::text) FROM pg_database, unnest(datacl) a
                WHERE datname = current_database() AND a::text NOT LIKE 'marketpulse_demo_ro=%'),
            (SELECT nspacl FROM pg_namespace WHERE nspname = 'macro_data'),
            (SELECT relacl FROM pg_class WHERE oid = 'macro_data.private_capture'::regclass),
            (SELECT proacl FROM pg_proc WHERE oid = 'public.digest(text,text)'::regprocedure)
    """).fetchone()


def test_unsafe_public_grant_aborts_initial_migration(shared_demo_database, install_demo):
    with shared_demo_database() as (_, db):
        _prepare_dev(db)
        db.execute("GRANT SELECT (metric) ON public.private_metrics TO PUBLIC")
        original = _dev_acl(db)
        with pytest.raises(psycopg.errors.RaiseException, match="non-DEMO relation or sequence"):
            install_demo(db)
        db.execute("ROLLBACK")
        assert db.execute("""
            SELECT to_regnamespace('marketpulse_demo'), to_regrole('marketpulse_demo_ro')
        """).fetchone() == (None, None)
        assert _dev_acl(db) == original
        assert db.execute("SELECT metric FROM public.private_metrics").fetchone() == (5,)


@pytest.mark.parametrize(("drift", "rejection"), [
    ("GRANT CREATE ON SCHEMA public TO PUBLIC", "unexpected effective database or schema"),
    ("GRANT SELECT ON TABLE public.private_metrics TO PUBLIC", "non-DEMO relation or sequence"),
    ("GRANT SELECT (metric) ON TABLE public.private_metrics TO PUBLIC", "non-DEMO relation or sequence"),
    ("GRANT SELECT ON TABLE macro_data.private_capture TO marketpulse_demo_ro", "non-DEMO relation or sequence"),
    ("GRANT SELECT ON TABLE pg_catalog.pg_authid TO PUBLIC", "widened access"),
    ("GRANT SELECT (subconninfo) ON pg_catalog.pg_subscription TO PUBLIC", "widened access"),
    ("GRANT USAGE ON SEQUENCE public.private_seq TO PUBLIC", "non-DEMO relation or sequence"),
    ("GRANT USAGE ON SCHEMA macro_data TO PUBLIC", "callable application routine"),
    ("GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) TO PUBLIC", "widened access"),
    ("CREATE FUNCTION pg_catalog.shared_fixture() RETURNS integer LANGUAGE sql AS 'SELECT 9'", "callable application routine"),
    ("GRANT pg_read_all_data TO marketpulse_demo_ro WITH INHERIT FALSE, SET TRUE", "unsafe attributes"),
    ("GRANT pg_write_all_data TO marketpulse_demo_ro WITH INHERIT TRUE, SET FALSE", "unsafe attributes"),
    ("GRANT marketpulse_demo_ro TO pg_read_all_data WITH INHERIT TRUE, SET FALSE", "unsafe attributes"),
    ("ALTER ROLE marketpulse_demo_ro CREATEDB", "unsafe attributes"),
    ("CREATE SCHEMA owned_by_demo AUTHORIZATION marketpulse_demo_ro", "unsafe attributes"),
    ("ALTER TABLE marketpulse_demo.macro_catalog ENABLE ROW LEVEL SECURITY", "independent"),
    ("ALTER TABLE marketpulse_demo.macro_observations ADD COLUMN unexpected text", "projection columns"),
    ("ALTER TABLE marketpulse_demo.macro_catalog DROP CONSTRAINT macro_catalog_series_key_key", "projection keys"),
    ("ALTER TABLE marketpulse_demo.macro_observations DROP CONSTRAINT macro_observations_pkey", "projection keys"),
    ("ALTER TABLE marketpulse_demo.macro_observations DROP CONSTRAINT macro_observations_indicator_id_fkey",
     "projection keys"),
    ("ALTER TABLE marketpulse_demo.macro_observations DROP CONSTRAINT macro_observations_indicator_id_fkey;"
     "ALTER TABLE marketpulse_demo.macro_observations ADD FOREIGN KEY (indicator_id) "
     "REFERENCES marketpulse_demo.macro_catalog ON DELETE CASCADE", "projection keys"),
    ("ALTER TABLE marketpulse_demo.macro_observations DROP CONSTRAINT macro_observations_indicator_id_fkey;"
     "ALTER TABLE marketpulse_demo.macro_observations ADD FOREIGN KEY (indicator_id) "
     "REFERENCES marketpulse_demo.macro_catalog NOT VALID", "projection keys"),
    ("ALTER TABLE marketpulse_demo.macro_observations DISABLE TRIGGER ALL", "foreign-key triggers"),
    ("GRANT SELECT ON TABLE marketpulse_demo.macro_catalog TO pg_read_all_data", "differ from SELECT only"),
    ("CREATE FOREIGN DATA WRAPPER fixture; GRANT USAGE ON FOREIGN DATA WRAPPER fixture TO PUBLIC",
     "foreign-data"),
    ("SELECT lo_from_bytea(7313, '\\x00'::bytea); GRANT SELECT ON LARGE OBJECT 7313 TO PUBLIC", "large-object"),
    ("CREATE FOREIGN DATA WRAPPER fixture; CREATE SERVER fixture FOREIGN DATA WRAPPER fixture;"
     "GRANT USAGE ON FOREIGN SERVER fixture TO PUBLIC", "foreign-data"),
    ("CREATE TABLE public.demo_ref (id text REFERENCES marketpulse_demo.macro_catalog ON DELETE CASCADE)", "independent"),
    ("CREATE FUNCTION macro_data.dev_trigger() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END';"
     "CREATE TRIGGER fixture BEFORE INSERT ON marketpulse_demo.macro_observations "
     "FOR EACH ROW EXECUTE FUNCTION macro_data.dev_trigger()", "independent"),
])
def test_verifier_detects_permission_and_projection_drift(shared_demo_database, install_demo, drift, rejection):
    with shared_demo_database() as (_, db):
        _prepare_dev(db)
        install_demo(db)
        db.execute(drift)
        with pytest.raises(psycopg.errors.RaiseException, match=rejection):
            db.execute("SELECT marketpulse_demo.assert_isolated()")
        with pytest.raises(psycopg.errors.RaiseException, match=rejection):
            install_demo(db)
        db.execute("ROLLBACK")
        # The rejected rerun did not quietly alter the conflicting baseline.
        with pytest.raises(psycopg.errors.RaiseException, match=rejection):
            db.execute("SELECT marketpulse_demo.assert_isolated()")


def test_verifier_detects_public_parameter_grant(shared_demo_database, install_demo):
    with shared_demo_database() as (_, db):
        install_demo(db)
        # Parameter ACLs are cluster-wide even on a disposable database.
        with db.transaction(force_rollback=True):
            db.execute("GRANT SET ON PARAMETER session_replication_role TO PUBLIC")
            with pytest.raises(psycopg.errors.RaiseException, match="parameter privileges"), db.transaction():
                db.execute("SELECT marketpulse_demo.assert_isolated()")
        db.execute("SELECT marketpulse_demo.assert_isolated()")


@pytest.mark.parametrize("extended", [False, True], ids=["column-statistics", "extended-statistics"])
def test_raw_statistics_acl_exposes_private_values_and_blocks_provisioning(
    shared_demo_database, install_demo, extended
):
    with shared_demo_database() as (dsn, db):
        marker = "synthetic private statistic"
        db.execute("""
            CREATE SCHEMA macro_data;
            CREATE TABLE macro_data.private_stats_fixture (marker text, category integer);
        """)
        db.execute("""
            INSERT INTO macro_data.private_stats_fixture
            SELECT %s, 1 FROM generate_series(1, 100)
        """, (marker,))
        db.execute("""
            CREATE STATISTICS macro_data.private_statistics (mcv)
                ON marker, category FROM macro_data.private_stats_fixture;
            ANALYZE macro_data.private_stats_fixture;
        """)
        if extended:
            statement = """
                SELECT items.values FROM pg_catalog.pg_statistic_ext_data s,
                    LATERAL pg_catalog.pg_mcv_list_items(s.stxdmcv) items
                WHERE s.stxoid = %s
            """
            oid_query = "SELECT oid FROM pg_catalog.pg_statistic_ext WHERE stxname = 'private_statistics'"
            grant = "GRANT SELECT ON pg_catalog.pg_statistic_ext_data TO PUBLIC"
            expected = [([marker, "1"],)]
        else:
            statement = "SELECT stavalues1::text FROM pg_catalog.pg_statistic WHERE starelid = %s AND staattnum = 1"
            oid_query = "SELECT 'macro_data.private_stats_fixture'::regclass::oid"
            grant = "GRANT SELECT (starelid, staattnum, stavalues1) ON pg_catalog.pg_statistic TO PUBLIC"
            expected = [(f'{{"{marker}"}}',)]
        (oid,) = db.execute(oid_query).fetchone()
        install_demo(db)
        db.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        with psycopg.connect(make_conninfo(dsn, user="marketpulse_demo_ro"), autocommit=True) as reader:
            assert reader.execute("SELECT * FROM pg_stats WHERE schemaname = 'macro_data'").fetchall() == []
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement, (oid,))
            db.execute(grant)
            assert reader.execute(statement, (oid,)).fetchall() == expected
            with pytest.raises(psycopg.errors.RaiseException, match="raw PostgreSQL statistics"):
                db.execute("SELECT marketpulse_demo.assert_isolated()")
            with pytest.raises(psycopg.errors.RaiseException, match="raw PostgreSQL statistics"):
                install_demo(db)
            db.execute("ROLLBACK")
            # The failed migration preserves the separately established catalog grant.
            assert reader.execute(statement, (oid,)).fetchall() == expected


@pytest.mark.parametrize("grant", [
    "GRANT SELECT ON net.http_request_queue TO PUBLIC",
    "GRANT SELECT (headers) ON net.http_request_queue TO PUBLIC",
    "GRANT SELECT (content) ON net._http_response TO PUBLIC",
    "GRANT INSERT ON net.http_request_queue TO PUBLIC",
    "GRANT USAGE ON SEQUENCE net.http_request_queue_id_seq TO PUBLIC",
    "GRANT EXECUTE ON FUNCTION net.http_get(text) TO PUBLIC",
    "GRANT EXECUTE ON FUNCTION net.http_collect_response(bigint) TO PUBLIC",
    "GRANT EXECUTE ON FUNCTION net.worker_restart() TO PUBLIC",
])
def test_pg_net_effective_public_grants_block_without_reading_or_sending(
    shared_demo_database, install_demo, grant
):
    with shared_demo_database() as (_, db):
        # Metadata fixture for the installed extension's relevant objects.
        # The queue/response are empty and none of the stubs perform network I/O.
        db.execute("""
            CREATE SCHEMA net;
            GRANT USAGE ON SCHEMA net TO PUBLIC;
            CREATE UNLOGGED TABLE net.http_request_queue (id bigserial, headers jsonb);
            CREATE UNLOGGED TABLE net._http_response (id bigint, content text);
            CREATE FUNCTION net.http_get(text) RETURNS bigint
            LANGUAGE sql SECURITY DEFINER AS 'SELECT 1::bigint';
            CREATE FUNCTION net.http_collect_response(bigint) RETURNS text
            LANGUAGE sql SECURITY DEFINER AS 'SELECT NULL::text';
            CREATE FUNCTION net.worker_restart() RETURNS void
            LANGUAGE plpgsql AS 'BEGIN RETURN; END';
            REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA net FROM PUBLIC;
        """)
        install_demo(db)
        db.execute(grant)
        with pytest.raises(psycopg.errors.RaiseException, match="effective pg_net access"):
            db.execute("SELECT marketpulse_demo.assert_isolated()")
        with pytest.raises(psycopg.errors.RaiseException, match="effective pg_net access"):
            install_demo(db)
        db.execute("ROLLBACK")
        # Failed provisioning never changes the managed grant to make itself pass.
        with pytest.raises(psycopg.errors.RaiseException, match="effective pg_net access"):
            db.execute("SELECT marketpulse_demo.assert_isolated()")


@pytest.mark.parametrize("domain_schema", ["public", "macro_data"])
def test_hidden_security_definer_reachable_by_domain_oid(shared_demo_database, install_demo, domain_schema):
    with shared_demo_database() as (dsn, db):
        _prepare_dev(db)
        # A nested domain using only built-in operations can coexist with DEMO.
        db.execute("""
            CREATE DOMAIN macro_data.positive AS integer CHECK (VALUE > 0);
            CREATE DOMAIN macro_data.nonzero AS macro_data.positive CHECK (VALUE <> 0);
        """)
        install_demo(db)
        db.execute("""
            CREATE FUNCTION macro_data.is_private_value(candidate integer) RETURNS boolean
            LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
            AS 'SELECT candidate = internal_value FROM macro_data.private_capture';
        """)
        domain = sql.Identifier(domain_schema, "guarded_fixture")
        db.execute(sql.SQL("""
            CREATE DOMAIN {} AS macro_data.nonzero CHECK (macro_data.is_private_value(VALUE))
        """).format(domain))
        domain_oid = db.execute("SELECT %s::regtype::oid", (domain.as_string(db),)).fetchone()[0]
        db.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        with psycopg.connect(make_conninfo(dsn, user="marketpulse_demo_ro"), autocommit=True) as reader:
            # SQL names for hidden types are inaccessible. The extended
            # protocol can still provide the type OID and run its CHECK.
            if domain_schema == "macro_data":
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute(sql.SQL("SELECT 9::{}").format(domain))
            else:
                assert reader.execute(sql.SQL("SELECT 9::{}").format(domain)).fetchone() == (9,)
            matched = reader.pgconn.exec_params(b"SELECT $1::integer", [b"9"], [domain_oid])
            mismatched = reader.pgconn.exec_params(b"SELECT $1::integer", [b"8"], [domain_oid])
            assert matched.status == pq.ExecStatus.TUPLES_OK and matched.get_value(0, 0) == b"9"
            assert mismatched.status == pq.ExecStatus.FATAL_ERROR
            assert mismatched.error_field(pq.DiagnosticField.SQLSTATE) == b"23514"
        with pytest.raises(psycopg.errors.RaiseException, match="reachable by OID"):
            db.execute("SELECT marketpulse_demo.assert_isolated()")
        with pytest.raises(psycopg.errors.RaiseException, match="reachable by OID"):
            install_demo(db)
        db.execute("ROLLBACK")


@pytest.mark.parametrize("wrapper", [False, True], ids=["definer", "invoker-wrapper"])
def test_hidden_enum_oid_can_reach_privileged_cast(shared_demo_database, install_demo, wrapper):
    with shared_demo_database() as (dsn, db):
        _prepare_dev(db)
        install_demo(db)
        db.execute("CREATE TYPE macro_data.private_enum AS ENUM ('fixture')")
        if wrapper:
            # The parsed body resolves the definer function before the reader
            # invokes the cast. A schema USAGE check on the reader misses it.
            db.execute("""
                CREATE FUNCTION macro_data.private_cast(macro_data.private_enum) RETURNS integer
                LANGUAGE sql SECURITY INVOKER RETURN macro_data.private_read()
            """)
        else:
            db.execute("""
                CREATE FUNCTION macro_data.private_cast(macro_data.private_enum) RETURNS integer
                LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
                AS 'SELECT internal_value FROM macro_data.private_capture'
            """)
        db.execute("""
            CREATE CAST (macro_data.private_enum AS integer)
                WITH FUNCTION macro_data.private_cast(macro_data.private_enum);
        """)
        enum_oid = db.execute("SELECT 'macro_data.private_enum'::regtype::oid").fetchone()[0]
        db.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        with psycopg.connect(make_conninfo(dsn, user="marketpulse_demo_ro"), autocommit=True) as reader:
            result = reader.pgconn.exec_params(b"SELECT $1::integer", [b"fixture"], [enum_oid])
            assert result.status == pq.ExecStatus.TUPLES_OK and result.get_value(0, 0) == b"9"
        with pytest.raises(psycopg.errors.RaiseException, match="reachable by OID"):
            db.execute("SELECT marketpulse_demo.assert_isolated()")


def test_hidden_text_search_oid_reaches_application_callback(shared_demo_database, install_demo):
    with shared_demo_database() as (dsn, db):
        _prepare_dev(db)
        db.execute("CREATE TEXT SEARCH DICTIONARY macro_data.safe_dict (TEMPLATE = pg_catalog.simple)")
        install_demo(db)
        db.execute("""
            CREATE FUNCTION macro_data.fixture_lexize(internal, internal, internal, internal) RETURNS internal
            LANGUAGE internal AS 'dsimple_lexize';
            CREATE TEXT SEARCH TEMPLATE macro_data.fixture_template (
                INIT = pg_catalog.dsimple_init, LEXIZE = macro_data.fixture_lexize
            );
            CREATE TEXT SEARCH DICTIONARY macro_data.fixture_dict (TEMPLATE = macro_data.fixture_template);
        """)
        db.execute("ALTER ROLE marketpulse_demo_ro LOGIN")
        oid = db.execute("SELECT 'macro_data.fixture_dict'::regdictionary::oid").fetchone()[0]
        with psycopg.connect(make_conninfo(dsn, user="marketpulse_demo_ro"), autocommit=True) as reader:
            assert reader.execute("SELECT ts_lexize(%s::oid::regdictionary, 'Fixture')", (oid,)).fetchone() == (["fixture"],)
        with pytest.raises(psycopg.errors.RaiseException, match="reachable by OID"):
            db.execute("SELECT marketpulse_demo.assert_isolated()")


def test_non_superuser_creator_has_only_postgresql_admin_membership(shared_demo_database, install_demo):
    with shared_demo_database() as (dsn, db):
        owner = db.info.dbname + "_owner"
        db.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEROLE").format(sql.Identifier(owner)))
        try:
            db.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(db.info.dbname), sql.Identifier(owner)
            ))
            with psycopg.connect(make_conninfo(dsn, user=owner), autocommit=True) as provisioner:
                install_demo(provisioner)
                provisioner.execute("SELECT marketpulse_demo.assert_isolated()")
                assert provisioner.execute("""
                    SELECT admin_option, inherit_option, set_option FROM pg_auth_members
                    WHERE roleid = 'marketpulse_demo_ro'::regrole AND member = current_user::regrole
                """).fetchone() == (True, False, False)
        finally:
            # Drop ownership in the disposable DB before removing its cluster role.
            db.execute(sql.SQL("ALTER DATABASE {} OWNER TO postgres").format(sql.Identifier(db.info.dbname)))
            db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(owner)))
            db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(owner)))
