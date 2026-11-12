-- One-time setup on a fresh and independently verified Supabase DEMO.
-- Use psql with ON_ERROR_STOP and load the verified official snapshot separately.
BEGIN;
SET LOCAL search_path = pg_catalog;

DO $$
BEGIN
    IF current_database() <> 'postgres'
        OR to_regnamespace('market_data') IS NOT NULL
        OR to_regnamespace('macro_data') IS NOT NULL
        OR to_regnamespace('news') IS NOT NULL
        OR to_regnamespace('marketpulse_demo') IS NOT NULL
        OR EXISTS (SELECT FROM pg_roles WHERE rolname = 'marketpulse_demo_ro')
    THEN
        RAISE EXCEPTION 'Expected the independently verified DEMO database';
    END IF;
END
$$;

CREATE SCHEMA marketpulse_demo;
REVOKE ALL ON SCHEMA marketpulse_demo FROM PUBLIC;
CREATE ROLE marketpulse_demo_ro NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE marketpulse_demo_ro SET default_transaction_read_only = on;
REVOKE CREATE, TEMPORARY ON DATABASE postgres FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO marketpulse_demo_ro;
GRANT USAGE ON SCHEMA marketpulse_demo TO marketpulse_demo_ro;

CREATE TABLE marketpulse_demo.macro_catalog (
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

CREATE TABLE marketpulse_demo.macro_observations (
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

REVOKE ALL ON marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations FROM PUBLIC;
REVOKE ALL ON marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations FROM marketpulse_demo_ro;
DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role']
    LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA marketpulse_demo FROM %I', api_role);
            EXECUTE format(
                'REVOKE ALL ON marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations FROM %I',
                api_role
            );
        END IF;
    END LOOP;
END
$$;
GRANT SELECT ON marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations TO marketpulse_demo_ro;
COMMIT;
