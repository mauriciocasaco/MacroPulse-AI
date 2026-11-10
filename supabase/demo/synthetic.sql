-- One-time setup against a fresh, independently verified DEMO project.
-- Use psql with ON_ERROR_STOP as the DEMO project administrator.
BEGIN;

DO $$
BEGIN
    IF current_database() <> 'postgres'
        OR to_regnamespace('market_data') IS NOT NULL
        OR to_regnamespace('news') IS NOT NULL
    THEN
        RAISE EXCEPTION 'Expected a fresh DEMO database';
    END IF;
END
$$;

CREATE SCHEMA marketpulse_demo;
REVOKE ALL ON SCHEMA marketpulse_demo FROM PUBLIC;

CREATE TABLE marketpulse_demo.synthetic_bars (
    series_id smallint NOT NULL CHECK (series_id BETWEEN 1 AND 3),
    bar_date date NOT NULL CHECK (bar_date >= DATE '2019-01-01' AND bar_date < DATE '2025-01-01'),
    close numeric(8, 2) NOT NULL CHECK (close > 0),
    generated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (series_id, bar_date)
);

INSERT INTO marketpulse_demo.synthetic_bars (series_id, bar_date, close)
SELECT series, DATE '2019-01-01' + day_index,
       (80 + series * 12 + day_index * 0.01 + ((day_index * (series + 2)) % 61) * 0.2)
FROM generate_series(1, 3) AS s(series)
CROSS JOIN generate_series(0, DATE '2025-01-01' - DATE '2019-01-01' - 1) AS d(day_index);

CREATE ROLE marketpulse_demo_ro NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE marketpulse_demo_ro SET default_transaction_read_only = on;
REVOKE CREATE, TEMPORARY ON DATABASE postgres FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO marketpulse_demo_ro;
GRANT USAGE ON SCHEMA marketpulse_demo TO marketpulse_demo_ro;
GRANT SELECT ON marketpulse_demo.synthetic_bars TO marketpulse_demo_ro;

COMMIT;
