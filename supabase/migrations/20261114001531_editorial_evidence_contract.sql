BEGIN;

CREATE ROLE macropulse_editorial_view_owner NOLOGIN NOINHERIT NOSUPERUSER
    NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE SCHEMA news_editorial;
REVOKE ALL ON SCHEMA news_editorial FROM PUBLIC, anon, authenticated, service_role, macropulse_online_ro;

CREATE TABLE news_editorial.reviewer (
    reviewer_id text PRIMARY KEY CHECK (btrim(reviewer_id) <> ''),
    identity_ref text NOT NULL UNIQUE CHECK (btrim(identity_ref) <> ''),
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE news_editorial.source_revision (
    record_id bigint REFERENCES news.article_records(id),
    source_version text CHECK (source_version ~ '^[0-9a-f]{64}$'),
    canonical_url text NOT NULL CHECK (canonical_url ~ '^https://[^/[:space:]]+[^[:space:]]*$'),
    source_id text NOT NULL REFERENCES news.sources(id),
    publisher text NOT NULL CHECK (btrim(publisher) <> ''),
    title text NOT NULL CHECK (btrim(title) <> ''),
    published_on date NOT NULL CHECK (isfinite(published_on)),
    observed_at timestamptz NOT NULL CHECK (isfinite(observed_at)),
    evidence_url text NOT NULL CHECK (evidence_url ~ '^https://[^/[:space:]]+[^[:space:]]*$'),
    locator text NOT NULL CHECK (btrim(locator) <> ''),
    provenance_ref text NOT NULL CHECK (btrim(provenance_ref) <> ''),
    tenant_scope text NOT NULL CHECK (tenant_scope IN ('public', 'private')),
    retired_at timestamptz CHECK (isfinite(retired_at) AND retired_at >= observed_at),
    PRIMARY KEY (record_id, source_version),
    CHECK (published_on <= (observed_at AT TIME ZONE 'UTC')::date)
);

CREATE UNIQUE INDEX news_editorial_current_source
    ON news_editorial.source_revision (record_id) WHERE retired_at IS NULL;
CREATE INDEX news_editorial_source_provider ON news_editorial.source_revision (source_id);

CREATE TABLE news_editorial.claim_revision (
    claim_id text CHECK (claim_id ~ '^[a-zA-Z0-9_-]{1,100}$'),
    claim_version text CHECK (claim_version ~ '^[0-9a-f]{64}$'),
    record_id bigint NOT NULL,
    source_version text NOT NULL,
    event text NOT NULL CHECK (event IN ('covid', 'ukraine', 'genai')),
    indicator text NOT NULL CHECK (indicator IN ('eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy')),
    measure text NOT NULL CHECK (btrim(measure) <> ''),
    unit text NOT NULL CHECK (btrim(unit) <> ''),
    geography text NOT NULL CHECK (btrim(geography) <> ''),
    relation text NOT NULL CHECK (relation IN ('direct', 'contextual', 'proxy')),
    evidence_type text NOT NULL CHECK (evidence_type IN ('observation', 'interpretation', 'historical_context', 'proxy')),
    event_linkage text NOT NULL CHECK (btrim(event_linkage) <> ''),
    period_start date NOT NULL CHECK (isfinite(period_start)),
    period_end date NOT NULL CHECK (isfinite(period_end)),
    summary text NOT NULL CHECK (length(btrim(summary)) BETWEEN 10 AND 600),
    qualifiers text NOT NULL CHECK (btrim(qualifiers) <> ''),
    attribution text NOT NULL CHECK (btrim(attribution) <> ''),
    origin text NOT NULL CHECK (origin IN ('original_factual', 'literal')),
    retired_at timestamptz CHECK (isfinite(retired_at)),
    PRIMARY KEY (claim_id, claim_version),
    FOREIGN KEY (record_id, source_version) REFERENCES news_editorial.source_revision,
    CHECK (period_start <= period_end),
    CHECK ((relation = 'proxy') = (evidence_type = 'proxy')),
    CHECK (relation <> 'direct' OR (
        evidence_type = 'observation' AND CASE indicator
            WHEN 'eur-usd' THEN measure = 'spot_reference_rate' AND unit = 'USD per EUR' AND geography = 'bilateral'
            WHEN 'm3-stock' THEN measure = 'm3_end_of_period_stock' AND unit IN ('EUR', 'EUR million', 'EUR billion')
            WHEN 'deposit-rate' THEN measure = 'deposit_facility_rate' AND unit = '%'
            WHEN 'real-gdp-qoq' THEN measure = 'real_gdp_qoq' AND unit = '%'
            WHEN 'hicp-yoy' THEN measure = 'headline_hicp_yoy' AND unit = '%'
        END
    ))
);

CREATE UNIQUE INDEX news_editorial_current_claim
    ON news_editorial.claim_revision (claim_id) WHERE retired_at IS NULL;
CREATE INDEX news_editorial_claim_source
    ON news_editorial.claim_revision (record_id, source_version);

CREATE TABLE news_editorial.rights_decision (
    claim_id text,
    claim_version text,
    use_kind text CHECK (use_kind IN ('factual_public', 'literal_public', 'excerpt_storage', 'embedding')),
    rights_version text CHECK (rights_version ~ '^[0-9a-f]{64}$'),
    decision text NOT NULL CHECK (decision IN ('allowed', 'denied', 'pending')),
    basis_url text NOT NULL CHECK (basis_url ~ '^https://[^/[:space:]]+[^[:space:]]*$'),
    basis text NOT NULL CHECK (btrim(basis) <> ''),
    conditions text NOT NULL CHECK (btrim(conditions) <> ''),
    decided_by text NOT NULL REFERENCES news_editorial.reviewer,
    decided_at timestamptz NOT NULL CHECK (isfinite(decided_at)),
    effective_at timestamptz NOT NULL CHECK (isfinite(effective_at) AND effective_at >= decided_at),
    expires_at timestamptz CHECK (isfinite(expires_at) AND expires_at > effective_at),
    revoked_at timestamptz CHECK (isfinite(revoked_at) AND revoked_at >= decided_at),
    PRIMARY KEY (claim_id, claim_version, use_kind, rights_version),
    FOREIGN KEY (claim_id, claim_version) REFERENCES news_editorial.claim_revision
);

CREATE UNIQUE INDEX news_editorial_current_rights
    ON news_editorial.rights_decision (claim_id, claim_version, use_kind) WHERE revoked_at IS NULL;
CREATE INDEX news_editorial_rights_actor ON news_editorial.rights_decision (decided_by);

CREATE TABLE news_editorial.editorial_review (
    claim_id text,
    claim_version text,
    rights_use text GENERATED ALWAYS AS ('factual_public'::text) STORED,
    rights_version text,
    reviewer_id text NOT NULL REFERENCES news_editorial.reviewer,
    review_evidence text NOT NULL CHECK (btrim(review_evidence) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    status text NOT NULL CHECK (status IN ('approved', 'pending', 'rejected')),
    decided_at timestamptz NOT NULL CHECK (isfinite(decided_at)),
    withdrawn_at timestamptz CHECK (isfinite(withdrawn_at) AND withdrawn_at >= decided_at),
    PRIMARY KEY (claim_id, claim_version, rights_version, decided_at),
    FOREIGN KEY (claim_id, claim_version, rights_use, rights_version) REFERENCES news_editorial.rights_decision
);

CREATE UNIQUE INDEX news_editorial_current_review
    ON news_editorial.editorial_review (claim_id, claim_version, rights_version) WHERE withdrawn_at IS NULL;
CREATE INDEX news_editorial_review_actor ON news_editorial.editorial_review (reviewer_id);

-- A decision binds the exact facts and rights that were supplied at review time.
-- Each row is insert-only except for the one-way withdrawal timestamp.
CREATE FUNCTION news_editorial.protect_versions() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    retirement_column text := TG_ARGV[0];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'editorial version history cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'editorial_review' THEN
        -- Stored generated columns are unavailable in NEW during BEFORE UPDATE.
        IF ROW(OLD.claim_id, OLD.claim_version, OLD.rights_version, OLD.reviewer_id,
               OLD.review_evidence, OLD.reason, OLD.status, OLD.decided_at)
            IS DISTINCT FROM
           ROW(NEW.claim_id, NEW.claim_version, NEW.rights_version, NEW.reviewer_id,
               NEW.review_evidence, NEW.reason, NEW.status, NEW.decided_at)
            OR OLD.withdrawn_at IS NOT NULL
            OR NEW.withdrawn_at IS NULL THEN
            RAISE EXCEPTION 'editorial versions are immutable except for irreversible retirement' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(OLD) - retirement_column) IS DISTINCT FROM (to_jsonb(NEW) - retirement_column)
        OR to_jsonb(OLD)->>retirement_column IS NOT NULL
        OR to_jsonb(NEW)->>retirement_column IS NULL THEN
        RAISE EXCEPTION 'editorial versions are immutable except for irreversible retirement' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION news_editorial.protect_versions() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER source_version_history BEFORE UPDATE OR DELETE ON news_editorial.source_revision
FOR EACH ROW EXECUTE FUNCTION news_editorial.protect_versions('retired_at');
CREATE TRIGGER claim_version_history BEFORE UPDATE OR DELETE ON news_editorial.claim_revision
FOR EACH ROW EXECUTE FUNCTION news_editorial.protect_versions('retired_at');
CREATE TRIGGER rights_version_history BEFORE UPDATE OR DELETE ON news_editorial.rights_decision
FOR EACH ROW EXECUTE FUNCTION news_editorial.protect_versions('revoked_at');
CREATE TRIGGER review_version_history BEFORE UPDATE OR DELETE ON news_editorial.editorial_review
FOR EACH ROW EXECUTE FUNCTION news_editorial.protect_versions('withdrawn_at');

REVOKE ALL ON ALL TABLES IN SCHEMA news_editorial FROM PUBLIC, anon, authenticated, service_role, macropulse_online_ro;
GRANT USAGE ON SCHEMA news_editorial, news, macropulse_online TO macropulse_editorial_view_owner;
GRANT SELECT ON ALL TABLES IN SCHEMA news_editorial TO macropulse_editorial_view_owner;
GRANT SELECT (id, canonical_url, publisher) ON news.articles TO macropulse_editorial_view_owner;
GRANT SELECT (id, article_id, source_id, reported_published_on, reported_published_at, retrieved_at,
              evidence_updated_at, is_synthetic, content_kind, storage_permission, public_display_allowed,
              rights_basis) ON news.article_records TO macropulse_editorial_view_owner;
GRANT SELECT ON macropulse_online.news_review TO macropulse_editorial_view_owner;

DO $$
DECLARE
    relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['reviewer', 'source_revision', 'claim_revision', 'rights_decision', 'editorial_review']
    LOOP
        EXECUTE format('ALTER TABLE news_editorial.%I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('CREATE POLICY view_owner_read ON news_editorial.%I FOR SELECT TO macropulse_editorial_view_owner USING (true)', relation);
    END LOOP;
END;
$$;

CREATE OR REPLACE VIEW macropulse_online.news_evidence
WITH (security_barrier = true, security_invoker = false) AS
SELECT s.canonical_url, s.source_id, s.published_on, c.event, c.indicator, c.period_start,
       c.period_end, c.geography, s.title, c.attribution, d.basis_url AS rights_url,
       s.locator, c.qualifiers, c.summary,
       c.claim_id, c.claim_version,
       greatest(s.observed_at, r.retrieved_at, d.effective_at, h.decided_at) AS available_at,
       c.relation, c.evidence_type, c.measure, c.unit, c.event_linkage, s.evidence_url
FROM news_editorial.claim_revision c
JOIN news_editorial.source_revision s USING (record_id, source_version)
JOIN news.article_records r ON r.id = s.record_id AND r.source_id = s.source_id
JOIN news.articles a ON a.id = r.article_id AND a.canonical_url = s.canonical_url
                    AND a.publisher = s.publisher
JOIN news_editorial.rights_decision d ON (d.claim_id, d.claim_version) = (c.claim_id, c.claim_version)
    AND d.use_kind = 'factual_public'
JOIN news_editorial.editorial_review h ON (h.claim_id, h.claim_version, h.rights_version) =
    (d.claim_id, d.claim_version, d.rights_version)
JOIN news_editorial.reviewer reviewer ON reviewer.reviewer_id = h.reviewer_id AND reviewer.active
JOIN news_editorial.reviewer rights_actor ON rights_actor.reviewer_id = d.decided_by AND rights_actor.active
WHERE c.retired_at IS NULL AND s.retired_at IS NULL AND s.tenant_scope = 'public'
  AND c.origin = 'original_factual' AND NOT r.is_synthetic
  AND c.period_end <= s.published_on
  AND coalesce(r.reported_published_on, (r.reported_published_at AT TIME ZONE 'UTC')::date) = s.published_on
  AND (r.evidence_updated_at IS NULL OR r.evidence_updated_at <= s.observed_at)
  AND d.decision = 'allowed' AND d.revoked_at IS NULL
  AND (d.expires_at IS NULL OR d.expires_at > statement_timestamp())
  AND h.status = 'approved' AND h.withdrawn_at IS NULL
  AND h.decided_at >= greatest(s.observed_at, r.retrieved_at, d.effective_at)
  AND h.decided_at <= statement_timestamp()
UNION ALL
-- The original shipped EIA row is frozen to the exact reviewed payload. No
-- candidate inserted into the legacy table can bypass the versioned policy.
SELECT old.*, NULL::text, NULL::text, NULL::timestamptz, 'contextual'::text,
       'historical_context'::text, NULL::text, NULL::text, NULL::text, old.canonical_url
FROM macropulse_online.news_review old
WHERE (old.canonical_url, old.source_id, old.published_on, old.event, old.indicator,
       old.period_start, old.period_end, old.geography, old.title, old.attribution, old.rights_url,
       old.locator, old.qualifiers, old.summary) = (
    'https://www.eia.gov/todayinenergy/detail.php?id=51618', 'eia', DATE '2022-03-14',
    'ukraine', 'hicp-yoy', DATE '2021-01-01', DATE '2021-12-31', 'OECD Europe',
    'Europe is a key destination for Russia’s energy exports', 'U.S. Energy Information Administration',
    'https://www.eia.gov/about/copyrights_reuse.php', 'Natural gas exports from Russia',
    'Pre-invasion energy exposure in OECD Europe. No euro-area HICP measure or causal estimate.',
    'EIA identified OECD Europe as the largest regional destination for Russian natural gas exports in 2021.'
)
AND EXISTS (
    SELECT FROM news.articles a JOIN news.article_records r ON r.article_id = a.id
    WHERE a.canonical_url = old.canonical_url AND r.source_id = old.source_id
      AND NOT r.is_synthetic AND r.public_display_allowed AND r.rights_basis IS NOT NULL
      AND r.content_kind IN ('excerpt', 'full')
      AND (r.storage_permission = 'full' OR r.content_kind = r.storage_permission)
      AND coalesce(r.reported_published_on, (r.reported_published_at AT TIME ZONE 'UTC')::date) = old.published_on
);

REVOKE ALL ON macropulse_online.news_evidence FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON macropulse_online.news_evidence TO macropulse_online_ro;

-- PostgreSQL 17 gives a non-superuser role creator ADMIN without SET.
-- Add and remove only the migration role's own, non-inherited membership.
GRANT macropulse_editorial_view_owner TO CURRENT_USER WITH SET TRUE, INHERIT FALSE;
GRANT CREATE ON SCHEMA macropulse_online TO macropulse_editorial_view_owner;
ALTER VIEW macropulse_online.news_evidence OWNER TO macropulse_editorial_view_owner;
REVOKE CREATE ON SCHEMA macropulse_online FROM macropulse_editorial_view_owner;
REVOKE macropulse_editorial_view_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;

COMMIT;
