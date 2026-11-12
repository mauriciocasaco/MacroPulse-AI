-- Run with psql -X -v ON_ERROR_STOP=1 on the independently verified shared database.
-- Any failed assertion rolls back DEMO changes. The reader starts with NOLOGIN.
BEGIN;
SET LOCAL search_path = pg_catalog;

DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 170000 OR current_user <> session_user THEN
        RAISE EXCEPTION 'DEMO requires PostgreSQL 17+ and a direct administrative identity';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'marketpulse_demo_ro') THEN
        CREATE ROLE marketpulse_demo_ro NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    ELSE
        ALTER ROLE marketpulse_demo_ro NOINHERIT;
    END IF;
    IF EXISTS (
        SELECT FROM pg_namespace WHERE nspname = 'marketpulse_demo'
            AND nspowner <> current_user::regrole
    ) OR current_user = 'marketpulse_demo_ro' THEN
        RAISE EXCEPTION 'DEMO schema must belong to the provisioning identity';
    END IF;
    IF EXISTS (
        SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'marketpulse_demo' AND p.proname = 'assert_isolated'
            AND p.pronargs = 0 AND p.proowner <> current_user::regrole
    ) THEN
        RAISE EXCEPTION 'DEMO verifier must belong to the provisioning identity';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS marketpulse_demo;

CREATE TABLE IF NOT EXISTS marketpulse_demo.macro_catalog (
    indicator_id text PRIMARY KEY,
    series_key text NOT NULL UNIQUE,
    captured_at timestamptz NOT NULL CHECK (
        captured_at >= TIMESTAMPTZ '1900-01-01 UTC' AND captured_at < TIMESTAMPTZ '9999-01-01 UTC'
    ),
    requested_start date NOT NULL CHECK (requested_start >= DATE '1900-01-01'),
    requested_end date NOT NULL CHECK (
        requested_end >= requested_start AND requested_end <= (captured_at AT TIME ZONE 'Europe/Berlin')::date
    ),
    response_sha256 text NOT NULL CHECK (response_sha256 ~ '^[a-f0-9]{64}$'),
    bundle_sha256 text NOT NULL CHECK (bundle_sha256 ~ '^[a-f0-9]{64}$'),
    deposit_coverage datemultirange,
    projected_rows integer NOT NULL CHECK (projected_rows BETWEEN 1 AND 5500),
    projection_sha256 text NOT NULL CHECK (projection_sha256 ~ '^[a-f0-9]{64}$'),
    CHECK ((indicator_id, series_key) IN (
        ('eur-usd', 'EXR.D.USD.EUR.SP00.A'),
        ('m3-stock', 'BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E'),
        ('deposit-rate', 'FM.B.U2.EUR.4F.KR.DFR.LEV'),
        ('real-gdp-qoq', 'namq_10_gdp?freq=Q&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ&geo=EA21'),
        ('hicp-yoy', 'prc_hicp_minr?freq=M&unit=RCH_A&coicop18=TOTAL&geo=EA')
    )),
    CHECK ((indicator_id = 'deposit-rate') = (deposit_coverage IS NOT NULL)),
    CHECK (deposit_coverage IS NULL OR (
        NOT isempty(deposit_coverage)
        AND deposit_coverage <@ datemultirange(daterange(
            requested_start, requested_end, '[]'
        ))
    ))
);

CREATE TABLE IF NOT EXISTS marketpulse_demo.macro_observations (
    indicator_id text NOT NULL REFERENCES marketpulse_demo.macro_catalog (indicator_id),
    period_raw text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    effective_date date,
    value_decimal numeric CHECK (value_decimal BETWEEN '-1e51'::numeric AND '1e51'::numeric
                                 AND scale(value_decimal) <= 80),
    status_kind text NOT NULL CHECK (status_kind IN (
        'normal', 'provisional', 'estimated', 'forecast', 'break',
        'missing', 'unknown', 'withheld', 'withdrawn'
    )),
    status_raw text CHECK (status_raw ~ '^[A-Za-z ]{1,32}$'),
    PRIMARY KEY (indicator_id, period_start),
    CHECK (period_start >= DATE '1900-01-01' AND period_end < DATE '9999-01-01' AND period_start <= period_end),
    CHECK ((indicator_id = 'deposit-rate') = (effective_date IS NOT NULL)),
    CHECK (effective_date IS NULL OR effective_date = period_start),
    CHECK ((value_decimal IS NULL) = (status_kind IN ('missing', 'unknown', 'withheld', 'withdrawn'))),
    CHECK (status_kind <> 'withheld' OR status_raw IS NULL),
    CHECK (CASE
        WHEN indicator_id = 'real-gdp-qoq' THEN extract(day FROM period_start) = 1
            AND extract(month FROM period_start) IN (1, 4, 7, 10)
            AND period_end = (period_start + INTERVAL '3 months - 1 day')::date
            AND period_raw = to_char(period_start, 'YYYY-"Q"Q')
        WHEN indicator_id IN ('m3-stock', 'hicp-yoy') THEN extract(day FROM period_start) = 1
            AND period_end = (period_start + INTERVAL '1 month - 1 day')::date
            AND period_raw = to_char(period_start, 'YYYY-MM')
        ELSE period_end = period_start AND period_raw = to_char(period_start, 'YYYY-MM-DD')
    END)
);

DO $$
DECLARE
    grantees text;
    column_list record;
BEGIN
    -- Normalize only the projection's ACLs, including column-level grants.
    SELECT 'PUBLIC, ' || string_agg(quote_ident(rolname), ', ') INTO grantees
    FROM pg_roles WHERE rolname IN ('marketpulse_demo_ro', 'anon', 'authenticated', 'service_role');
    EXECUTE 'REVOKE ALL ON SCHEMA marketpulse_demo FROM ' || grantees;
    EXECUTE 'REVOKE ALL ON marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations FROM ' || grantees;
    FOR column_list IN
        SELECT c.relname, string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) AS names
        FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE c.relnamespace = 'marketpulse_demo'::regnamespace
            AND c.relname IN ('macro_catalog', 'macro_observations')
            AND a.attnum > 0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        GROUP BY c.relname
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES (%s) ON marketpulse_demo.%I FROM %s',
            column_list.names, column_list.relname, grantees
        );
    END LOOP;
END
$$;
GRANT USAGE ON SCHEMA marketpulse_demo TO marketpulse_demo_ro;
GRANT SELECT ON marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations TO marketpulse_demo_ro;

-- Also called by the importer immediately before it writes. The reader cannot
-- execute this invoker routine. Check application ACLs and sensitive shared surfaces.
CREATE OR REPLACE FUNCTION marketpulse_demo.assert_isolated()
RETURNS void
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    owner_oid oid := current_user::regrole;
    reader oid := 'marketpulse_demo_ro'::regrole;
    demo oid := 'marketpulse_demo'::regnamespace;
    catalog oid := 'marketpulse_demo.macro_catalog'::regclass;
    observations oid := 'marketpulse_demo.macro_observations'::regclass;
    verifier oid := 'marketpulse_demo.assert_isolated()'::regprocedure;
    database_oid oid := (SELECT oid FROM pg_database WHERE datname = current_database());
    keys_valid boolean;
    foreign_key oid;
    referenced_index oid;
    table_privileges constant text := 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN';
    column_privileges constant text := 'SELECT, INSERT, UPDATE, REFERENCES';
BEGIN
    IF current_user <> session_user OR NOT EXISTS (
        SELECT FROM pg_namespace WHERE oid = demo AND nspowner = owner_oid
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: verification requires the schema owner';
    END IF;
    IF NOT EXISTS (
        SELECT FROM pg_roles WHERE oid = reader
            AND NOT (rolsuper OR rolinherit OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) OR EXISTS (
        SELECT FROM pg_auth_members WHERE member = reader OR grantor = reader
            OR (roleid = reader AND NOT (
                -- PostgreSQL grants this admin-only membership when a
                -- non-superuser with CREATEROLE provisions the role.
                member = owner_oid AND grantor = 10
                AND admin_option AND NOT inherit_option AND NOT set_option
            ))
    ) OR EXISTS (
        SELECT FROM pg_shdepend d
        WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = reader AND d.deptype = 'o'
            AND NOT (d.dbid = database_oid AND (
                (d.classid = 'pg_class'::regclass AND EXISTS (
                    SELECT FROM pg_class c WHERE c.oid = d.objid AND c.relpersistence = 't'
                ))
                OR (d.classid = 'pg_type'::regclass AND EXISTS (
                    SELECT FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                    WHERE t.oid = d.objid AND n.nspname ~ '^pg_temp_[0-9]+$'
                ))
                OR d.classid = 'pg_largeobject'::regclass
            ))
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: reader has unsafe attributes, memberships or ownership';
    END IF;
    IF NOT has_database_privilege(reader, database_oid, 'CONNECT')
        OR has_database_privilege(reader, database_oid, 'CREATE, CONNECT WITH GRANT OPTION, TEMPORARY WITH GRANT OPTION')
        OR NOT has_schema_privilege(reader, demo, 'USAGE')
        OR EXISTS (
            SELECT FROM pg_namespace
            WHERE nspname !~ '^pg_(toast_)?temp_[0-9]+$'
                AND has_schema_privilege(reader, oid, 'CREATE, USAGE WITH GRANT OPTION')
        )
    THEN
        RAISE EXCEPTION 'DEMO NO-GO: unexpected effective database or schema privileges (including PUBLIC)';
    END IF;

    IF EXISTS (
        SELECT FROM pg_class c
        WHERE c.oid IN (catalog, observations)
            AND (c.relkind <> 'r' OR c.relpersistence <> 'p' OR c.relowner <> owner_oid
                OR c.relispartition OR c.relrowsecurity OR c.relforcerowsecurity OR c.relhasrules
                OR c.relam <> (SELECT oid FROM pg_am WHERE amname = 'heap'))
    ) OR EXISTS (
        SELECT FROM pg_class WHERE relnamespace = demo AND relkind NOT IN ('i', 'I')
            AND oid NOT IN (catalog, observations)
    ) OR EXISTS (
        SELECT FROM pg_proc WHERE pronamespace = demo AND oid <> verifier
    ) OR EXISTS (
        SELECT FROM pg_inherits WHERE inhrelid IN (catalog, observations) OR inhparent IN (catalog, observations)
    ) OR EXISTS (
        SELECT FROM pg_trigger WHERE tgrelid IN (catalog, observations) AND NOT tgisinternal
    ) OR EXISTS (
        SELECT FROM pg_constraint WHERE contype = 'f' AND (
            (conrelid IN (catalog, observations) OR confrelid IN (catalog, observations))
            AND NOT (conrelid = observations AND confrelid = catalog)
        )
    ) OR EXISTS (
        SELECT FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid IN (catalog, observations) AND (
            i.indexprs IS NOT NULL OR i.indpred IS NOT NULL
            OR c.relam <> (SELECT oid FROM pg_am WHERE amname = 'btree')
            OR EXISTS (SELECT FROM unnest(i.indclass) AS opclass(oid) WHERE opclass.oid >= 16384)
        )
    ) OR EXISTS (
        SELECT FROM pg_constraint c JOIN pg_depend d ON d.classid = 'pg_constraint'::regclass AND d.objid = c.oid
        WHERE c.conrelid IN (catalog, observations) AND d.refobjid >= 16384
            AND d.refclassid IN ('pg_proc'::regclass, 'pg_operator'::regclass, 'pg_type'::regclass)
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: projection is not independent, owner-controlled storage';
    END IF;

    IF EXISTS (
        WITH expected (rel, cols) AS (VALUES
            (catalog, ARRAY['indicator_id:text', 'series_key:text', 'captured_at:timestamp with time zone',
                'requested_start:date', 'requested_end:date', 'response_sha256:text', 'bundle_sha256:text',
                'deposit_coverage:datemultirange', 'projected_rows:integer', 'projection_sha256:text']),
            (observations, ARRAY['indicator_id:text', 'period_raw:text', 'period_start:date', 'period_end:date',
                'effective_date:date', 'value_decimal:numeric', 'status_kind:text', 'status_raw:text'])
        )
        SELECT FROM expected e
        WHERE e.cols IS DISTINCT FROM (
            SELECT array_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod) ORDER BY a.attnum)
            FROM pg_attribute a WHERE a.attrelid = e.rel AND a.attnum > 0 AND NOT a.attisdropped
        ) OR EXISTS (
            SELECT FROM pg_attribute a WHERE a.attrelid = e.rel AND a.attnum > 0 AND NOT a.attisdropped
                AND (a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> '' OR a.attcollation NOT IN (0, 100))
        )
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: unexpected projection columns or defaults';
    END IF;

    WITH expected (rel, name, kind, cols, target) AS (VALUES
        (catalog, 'macro_catalog_pkey', 'p', ARRAY['indicator_id'], 0::oid),
        (catalog, 'macro_catalog_series_key_key', 'u', ARRAY['series_key'], 0::oid),
        (observations, 'macro_observations_pkey', 'p', ARRAY['indicator_id', 'period_start'], 0::oid),
        (observations, 'macro_observations_indicator_id_fkey', 'f', ARRAY['indicator_id'], catalog)
    )
    SELECT count(*) = 4,
        max(c.oid) FILTER (WHERE e.kind = 'f'),
        max(c.conindid) FILTER (WHERE e.kind = 'p' AND e.rel = catalog)
    INTO keys_valid, foreign_key, referenced_index
    FROM expected e
    JOIN pg_constraint c ON c.conrelid = e.rel AND c.conname = e.name AND c.contype::text = e.kind
    JOIN pg_index i ON i.indexrelid = c.conindid
    -- PostgreSQL 18 added conenforced. PostgreSQL 17 enforces these key types.
    WHERE c.convalidated AND COALESCE((to_jsonb(c)->>'conenforced')::boolean, true)
        AND NOT c.condeferrable AND NOT c.condeferred AND c.confrelid = e.target
        AND e.cols = ARRAY(
            SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(num, position)
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num
            ORDER BY k.position
        )
        AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
        AND (e.kind <> 'p' OR i.indisprimary)
        AND (e.kind <> 'f' OR (
            c.confupdtype = 'a' AND c.confdeltype = 'a' AND c.confmatchtype = 's'
            AND i.indrelid = e.target AND i.indisprimary
            AND c.confkey = ARRAY(
                SELECT a.attnum FROM pg_attribute a
                WHERE a.attrelid = catalog AND a.attname = 'indicator_id' AND NOT a.attisdropped
            )
        ));
    IF NOT keys_valid THEN
        RAISE EXCEPTION 'DEMO NO-GO: missing or incompatible projection keys or foreign key';
    END IF;

    IF NOT (
        WITH expected (rel, linked, routine, event) AS (VALUES
            (catalog, observations, 'pg_catalog."RI_FKey_noaction_del"()'::regprocedure, 9),
            (catalog, observations, 'pg_catalog."RI_FKey_noaction_upd"()'::regprocedure, 17),
            (observations, catalog, 'pg_catalog."RI_FKey_check_ins"()'::regprocedure, 5),
            (observations, catalog, 'pg_catalog."RI_FKey_check_upd"()'::regprocedure, 17)
        )
        SELECT count(*) = 4 AND count(DISTINCT (t.tgrelid, t.tgfoid, t.tgtype)) = 4
            AND bool_and(t.tgisinternal AND t.tgconstraint = foreign_key
                AND t.tgconstrindid = referenced_index AND t.tgenabled = 'O'
                AND NOT t.tgdeferrable AND NOT t.tginitdeferred AND t.tgnargs = 0 AND t.tgqual IS NULL
                AND EXISTS (
                    SELECT FROM expected e WHERE t.tgrelid = e.rel AND t.tgconstrrelid = e.linked
                        AND t.tgfoid = e.routine AND t.tgtype = e.event
                ))
        FROM pg_trigger t WHERE t.tgrelid IN (catalog, observations)
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: incompatible projection foreign-key triggers';
    END IF;

    IF EXISTS (
        SELECT FROM pg_class c
        WHERE c.oid IN (catalog, observations) AND (
            NOT has_table_privilege(reader, c.oid, 'SELECT')
            OR has_table_privilege(reader, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, SELECT WITH GRANT OPTION')
            OR has_any_column_privilege(reader, c.oid, 'INSERT, UPDATE, REFERENCES, SELECT WITH GRANT OPTION')
        )
    ) OR has_function_privilege(reader, verifier, 'EXECUTE') OR EXISTS (
        SELECT FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
        WHERE n.oid = demo AND a.grantee NOT IN (owner_oid, reader)
    ) OR EXISTS (
        SELECT FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE c.oid IN (catalog, observations) AND a.grantee NOT IN (owner_oid, reader)
    ) OR EXISTS (
        SELECT FROM pg_attribute c CROSS JOIN LATERAL aclexplode(c.attacl) a
        WHERE c.attrelid IN (catalog, observations) AND a.grantee <> owner_oid
    ) OR EXISTS (
        SELECT FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
        WHERE p.oid = verifier AND (p.proowner <> owner_oid OR a.grantee <> owner_oid)
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: reader privileges in the projection differ from SELECT only';
    END IF;

    -- pg_net may grant PUBLIC access to the worker queue and responses. The
    -- queue can contain clear-text headers, URLs and bodies from DEV. Never
    -- read these rows or invoke network functions to check the boundary.
    IF EXISTS (
        SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'net'
            AND CASE
                WHEN c.relkind = 'S' AND c.relname = 'http_request_queue_id_seq' THEN
                    has_sequence_privilege(reader, c.oid, 'USAGE, SELECT, UPDATE')
                WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f')
                    AND c.relname IN ('http_request_queue', '_http_response') THEN
                    has_table_privilege(reader, c.oid, table_privileges)
                    OR has_any_column_privilege(reader, c.oid, column_privileges)
                ELSE false
            END
    ) OR EXISTS (
        SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'net'
            AND (starts_with(p.proname, 'http_') OR starts_with(p.proname, '_http_')
                OR p.proname = 'worker_restart' OR p.prosecdef)
            AND has_schema_privilege(reader, n.oid, 'USAGE')
            AND has_function_privilege(reader, p.oid, 'EXECUTE')
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: effective pg_net access to request/response data or worker operations';
    END IF;

    -- DEV schemas, public and any other application schema remain unchanged.
    -- Include latent ACLs in hidden namespaces, especially for sequences that
    -- can be referenced by OID. Session-local objects are allowed.
    IF EXISTS (
        SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.oid NOT IN (catalog, observations)
            AND c.relpersistence <> 't'
            AND (n.nspname NOT IN ('pg_catalog', 'information_schema') OR c.oid >= 16384)
            AND CASE
                WHEN c.relkind = 'S' THEN has_sequence_privilege(reader, c.oid, 'USAGE, SELECT, UPDATE')
                WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f', 't') THEN
                    has_table_privilege(reader, c.oid, table_privileges)
                    OR has_any_column_privilege(reader, c.oid, column_privileges)
                ELSE false
            END
            -- pg_stat_statements hides other roles' query text and IDs from
            -- ordinary roles. Only allow its extension-owned read-only views.
            AND NOT (
                c.relkind = 'v' AND c.relname IN ('pg_stat_statements', 'pg_stat_statements_info')
                AND EXISTS (
                    SELECT FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                        AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                        AND e.extname = 'pg_stat_statements'
                )
                AND NOT has_table_privilege(reader, c.oid,
                    'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, SELECT WITH GRANT OPTION')
                AND NOT has_any_column_privilege(reader, c.oid, 'INSERT, UPDATE, REFERENCES, SELECT WITH GRANT OPTION')
            )
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: effective privileges on a non-DEMO relation or sequence';
    END IF;

    -- Raw statistics can expose values from otherwise inaccessible DEV tables.
    IF has_any_column_privilege(reader, 'pg_catalog.pg_statistic', 'SELECT')
        OR has_any_column_privilege(reader, 'pg_catalog.pg_statistic_ext_data', 'SELECT')
    THEN
        RAISE EXCEPTION 'DEMO NO-GO: effective read access to raw PostgreSQL statistics';
    END IF;

    -- Protect known catalog surfaces for credentials or server file access.
    IF has_any_column_privilege(reader, 'pg_catalog.pg_authid', 'SELECT')
        OR has_any_column_privilege(reader, 'pg_catalog.pg_shadow', 'SELECT')
        OR has_any_column_privilege(reader, 'pg_catalog.pg_user_mapping', 'SELECT')
        OR has_any_column_privilege(reader, 'pg_catalog.pg_largeobject', 'SELECT')
        OR has_column_privilege(reader, 'pg_catalog.pg_subscription', 'subconninfo', 'SELECT')
        OR EXISTS (
            SELECT FROM pg_proc p
            WHERE p.pronamespace = 'pg_catalog'::regnamespace
                AND p.proname IN ('pg_read_file', 'pg_read_binary_file', 'lo_import', 'lo_export')
                AND has_function_privilege(reader, p.oid, 'EXECUTE')
        )
    THEN
        RAISE EXCEPTION 'DEMO NO-GO: widened access to PostgreSQL credentials or server files';
    END IF;

    -- Built-in invoker routines and extension-owned invoker routines can
    -- coexist with the reader. Check named application and definer routines.
    IF EXISTS (
        SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE has_schema_privilege(reader, n.oid, 'USAGE')
            AND has_function_privilege(reader, p.oid, 'EXECUTE')
            AND (
                p.prosecdef
                OR ((n.nspname NOT IN ('pg_catalog', 'information_schema') OR p.oid >= 16384)
                    AND NOT EXISTS (
                        SELECT FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                            AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
                    ))
            )
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: callable application routine or privileged routine';
    END IF;

    -- These entry points can invoke callbacks by OID without schema lookup.
    -- An invoker callback can wrap a definer call, including in dynamic SQL.
    -- Allow built-ins and trusted extension invokers here; reject application
    -- callbacks without trying to infer all calls from their routine bodies.
    IF EXISTS (
        WITH domain_refs AS (
            SELECT d.refclassid, d.refobjid
            FROM pg_constraint c JOIN pg_depend d ON d.objid = c.oid
            WHERE c.contypid <> 0 AND c.contype = 'c' AND d.classid = 'pg_constraint'::regclass
                AND d.refclassid IN ('pg_proc'::regclass, 'pg_operator'::regclass)
        ), callbacks (routine) AS (
            SELECT castfunc FROM pg_cast WHERE castfunc <> 0
            UNION ALL
            SELECT refobjid FROM domain_refs WHERE refclassid = 'pg_proc'::regclass
            UNION ALL
            SELECT o.oprcode FROM domain_refs d JOIN pg_operator o ON o.oid = d.refobjid
            WHERE d.refclassid = 'pg_operator'::regclass
            UNION ALL
            SELECT unnest(ARRAY[tmplinit, tmpllexize]) FROM pg_ts_template
            UNION ALL
            SELECT unnest(ARRAY[prsstart, prstoken, prsend, prsheadline, prslextype]) FROM pg_ts_parser
        )
        SELECT FROM callbacks c JOIN pg_proc p ON p.oid = c.routine
        WHERE p.prosecdef OR (p.oid >= 16384 AND NOT EXISTS (
            SELECT FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
        ))
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: application or privileged callback reachable by OID';
    END IF;

    IF current_setting('lo_compat_privileges') <> 'off'
        OR EXISTS (
            SELECT FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) AS setting(value)
            WHERE s.setdatabase IN (0, database_oid) AND s.setrole IN (0, reader)
                AND starts_with(setting.value, 'lo_compat_privileges=') AND setting.value <> 'lo_compat_privileges=off'
        )
        OR EXISTS (
            SELECT FROM pg_largeobject_metadata m CROSS JOIN LATERAL aclexplode(m.lomacl) a
            WHERE m.lomowner <> reader AND a.grantee IN (0, reader)
                AND a.privilege_type IN ('SELECT', 'UPDATE')
        )
        OR EXISTS (SELECT FROM pg_foreign_server WHERE has_server_privilege(reader, oid, 'USAGE'))
        OR EXISTS (SELECT FROM pg_foreign_data_wrapper WHERE has_foreign_data_wrapper_privilege(reader, oid, 'USAGE'))
        OR EXISTS (SELECT FROM pg_tablespace WHERE has_tablespace_privilege(reader, oid, 'CREATE'))
        OR EXISTS (SELECT FROM pg_parameter_acl WHERE has_parameter_privilege(reader, parname, 'SET, ALTER SYSTEM'))
    THEN
        RAISE EXCEPTION 'DEMO NO-GO: effective large-object, foreign-data, tablespace or parameter privileges';
    END IF;

    -- Direct ACLs can be latent when a namespace is hidden. The only direct
    -- grants retained for the reader are CONNECT/TEMPORARY, USAGE on DEMO
    -- and public, and the two SELECTs. The checks above constrain grant options.
    IF EXISTS (
        SELECT FROM pg_shdepend d
        WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = reader AND d.deptype = 'a'
            AND NOT (
                (d.dbid = 0 AND d.classid = 'pg_database'::regclass AND d.objid = database_oid)
                OR (d.dbid = database_oid AND d.objsubid = 0 AND (
                    (d.classid = 'pg_namespace'::regclass AND d.objid IN (
                        demo, COALESCE(to_regnamespace('public')::oid, 0)
                    ))
                    OR (d.classid = 'pg_class'::regclass AND d.objid IN (catalog, observations))
                ))
            )
    ) THEN
        RAISE EXCEPTION 'DEMO NO-GO: direct ACL outside the approved projection scope';
    END IF;
END
$$;

DO $$
DECLARE
    grantees text;
BEGIN
    SELECT 'PUBLIC, ' || string_agg(quote_ident(rolname), ', ') INTO grantees
    FROM pg_roles WHERE rolname IN ('marketpulse_demo_ro', 'anon', 'authenticated', 'service_role');
    EXECUTE 'REVOKE ALL ON FUNCTION marketpulse_demo.assert_isolated() FROM ' || grantees;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO marketpulse_demo_ro', current_database());
    EXECUTE format(
        'ALTER ROLE marketpulse_demo_ro IN DATABASE %I SET default_transaction_read_only = on', current_database()
    );
    EXECUTE format('ALTER ROLE marketpulse_demo_ro IN DATABASE %I SET search_path = pg_catalog', current_database());
END
$$;
SELECT marketpulse_demo.assert_isolated();
COMMIT;
