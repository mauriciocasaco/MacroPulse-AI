BEGIN;

-- Apply as the owner of the source relations. The public login reads live
-- views with an explicit column boundary and never owns these objects.
CREATE ROLE macropulse_online_ro NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOREPLICATION NOBYPASSRLS;
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO macropulse_online_ro', current_database());
    EXECUTE format('ALTER ROLE macropulse_online_ro IN DATABASE %I SET default_transaction_read_only = on',
                   current_database());
    EXECUTE format('ALTER ROLE macropulse_online_ro IN DATABASE %I SET search_path = pg_catalog',
                   current_database());
END;
$$;

CREATE SCHEMA macropulse_online;
REVOKE ALL ON SCHEMA macropulse_online FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA macropulse_online TO macropulse_online_ro;

CREATE VIEW macropulse_online.series AS
SELECT indicator_id, title, frequency, unit, multiplier, provider, series_key,
       geography, adjustment, source_url, reuse_url, attribution
FROM macro_data.series
WHERE indicator_id IN ('eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy');

CREATE VIEW macropulse_online.capture WITH (security_barrier = true) AS
SELECT c.capture_id, c.indicator_id, c.requested_start, c.requested_end, c.captured_at,
       c.outcome, c.stale_observations,
       jsonb_build_object('version_scope', c.source_attributes->>'version_scope') AS source_attributes
FROM macro_data.capture c JOIN macropulse_online.series s USING (indicator_id)
WHERE c.outcome = 'success';

CREATE VIEW macropulse_online.observation_revision WITH (security_barrier = true) AS
SELECT r.revision_id, r.indicator_id, r.period_raw, r.period_start, r.period_end, r.effective_date,
       CASE WHEN s.provider = 'ecb' AND r.confidentiality_raw = 'F'
                  OR s.provider = 'eurostat' AND r.confidentiality_raw IS NULL
            THEN r.value_decimal END AS value_decimal,
       CASE WHEN s.provider = 'ecb' AND r.confidentiality_raw = 'F'
                  OR s.provider = 'eurostat' AND r.confidentiality_raw IS NULL
            THEN r.status_raw END AS status_raw,
       CASE WHEN s.provider = 'ecb' AND r.confidentiality_raw = 'F'
                  OR s.provider = 'eurostat' AND r.confidentiality_raw IS NULL
            THEN r.status_kind ELSE 'withheld' END AS status_kind,
       CASE WHEN s.provider = 'ecb' AND r.confidentiality_raw = 'F' THEN 'F'
            WHEN s.provider = 'eurostat' AND r.confidentiality_raw IS NULL THEN NULL
            ELSE 'withheld' END AS confidentiality_raw,
       r.provider_valid_from, r.created_capture_id
FROM macro_data.observation_revision r JOIN macropulse_online.series s USING (indicator_id)
WHERE r.provider_valid_from IS NULL;

CREATE VIEW macropulse_online.revision_capture AS
SELECT rc.revision_id, rc.capture_id, rc.indicator_id
FROM macro_data.revision_capture rc JOIN macropulse_online.series s USING (indicator_id);

-- Only independently reviewed summaries can enter public Research. Current
-- record rights are checked at query time. Source bodies and vectors stay private.
CREATE TABLE macropulse_online.news_review (
    canonical_url text NOT NULL,
    source_id text NOT NULL,
    published_on date NOT NULL,
    event text NOT NULL CHECK (event IN ('covid', 'ukraine', 'genai')),
    indicator text NOT NULL CHECK (indicator IN ('eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy')),
    period_start date NOT NULL,
    period_end date NOT NULL,
    geography text NOT NULL,
    title text NOT NULL,
    attribution text NOT NULL,
    rights_url text NOT NULL,
    locator text NOT NULL,
    qualifiers text NOT NULL,
    summary text NOT NULL CHECK (length(summary) BETWEEN 10 AND 600),
    PRIMARY KEY (canonical_url, event, indicator, period_start, period_end),
    CHECK (period_start <= period_end AND period_end <= published_on)
);

INSERT INTO macropulse_online.news_review VALUES (
    'https://www.eia.gov/todayinenergy/detail.php?id=51618', 'eia', '2022-03-14',
    'ukraine', 'hicp-yoy', '2021-01-01', '2021-12-31', 'OECD Europe',
    'Europe is a key destination for Russia’s energy exports', 'U.S. Energy Information Administration',
    'https://www.eia.gov/about/copyrights_reuse.php', 'Natural gas exports from Russia',
    'Pre-invasion energy exposure in OECD Europe. No euro-area HICP measure or causal estimate.',
    'EIA identified OECD Europe as the largest regional destination for Russian natural gas exports in 2021.'
);

CREATE VIEW macropulse_online.news_evidence WITH (security_barrier = true) AS
SELECT review.*
FROM macropulse_online.news_review review
WHERE EXISTS (
    SELECT FROM news.articles a JOIN news.article_records r ON r.article_id = a.id
    WHERE a.canonical_url = review.canonical_url AND r.source_id = review.source_id
      AND NOT r.is_synthetic AND r.public_display_allowed AND r.rights_basis IS NOT NULL
      AND r.content_kind IN ('excerpt', 'full')
      AND (r.storage_permission = 'full' OR r.content_kind = r.storage_permission)
      AND coalesce(r.reported_published_on, (r.reported_published_at AT TIME ZONE 'UTC')::date) = review.published_on
);

REVOKE ALL ON ALL TABLES IN SCHEMA macropulse_online FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON macropulse_online.series, macropulse_online.capture, macropulse_online.observation_revision,
    macropulse_online.revision_capture, macropulse_online.news_evidence TO macropulse_online_ro;

COMMIT;
