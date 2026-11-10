CREATE SCHEMA macro_data;

CREATE TABLE macro_data.schema_migrations (
    version text PRIMARY KEY,
    checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE macro_data.series (
    indicator_id text PRIMARY KEY CHECK (indicator_id <> ''),
    provider text NOT NULL CHECK (provider IN ('ecb', 'eurostat')),
    dataset text NOT NULL,
    series_key text NOT NULL UNIQUE,
    contract_version integer NOT NULL CHECK (contract_version > 0),
    frequency text NOT NULL CHECK (frequency IN ('daily', 'monthly', 'quarterly', 'effective_date')),
    unit text NOT NULL,
    multiplier smallint NOT NULL,
    geography text NOT NULL,
    adjustment text NOT NULL,
    title text NOT NULL,
    source_url text NOT NULL,
    reuse_url text NOT NULL,
    attribution text NOT NULL,
    dimensions jsonb NOT NULL CHECK (jsonb_typeof(dimensions) = 'object')
);

CREATE TABLE macro_data.capture (
    capture_id uuid PRIMARY KEY,
    indicator_id text NOT NULL REFERENCES macro_data.series (indicator_id),
    requested_start date NOT NULL,
    requested_end date NOT NULL,
    captured_at timestamptz NOT NULL CHECK (isfinite(captured_at)),
    request_url text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('success', 'failed')),
    http_status smallint CHECK (http_status BETWEEN 100 AND 599),
    attempts smallint NOT NULL CHECK (attempts > 0),
    error_code text,
    response_sha256 text CHECK (response_sha256 ~ '^[0-9a-f]{64}$'),
    response_body bytea CHECK (octet_length(response_body) <= 8388608),
    source_attributes jsonb NOT NULL CHECK (jsonb_typeof(source_attributes) = 'object'),
    observations integer NOT NULL CHECK (observations >= 0),
    new_revisions integer NOT NULL CHECK (new_revisions BETWEEN 0 AND observations),
    stale_observations integer NOT NULL CHECK (stale_observations BETWEEN 0 AND observations),
    UNIQUE (capture_id, indicator_id),
    CHECK (isfinite(requested_start) AND isfinite(requested_end) AND requested_start <= requested_end),
    CHECK (new_revisions + stale_observations <= observations),
    CHECK (
        (outcome = 'success' AND http_status IS NOT DISTINCT FROM 200 AND error_code IS NULL
            AND response_sha256 IS NOT NULL AND response_body IS NOT NULL)
        OR (outcome = 'failed' AND error_code IS NOT NULL AND response_body IS NULL AND observations = 0)
    )
);

CREATE TABLE macro_data.observation_revision (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    indicator_id text NOT NULL REFERENCES macro_data.series (indicator_id),
    period_raw text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    effective_date date,
    value_decimal numeric CHECK (value_decimal BETWEEN '-1e51'::numeric AND '1e51'::numeric),
    value_raw text,
    status_raw text,
    status_kind text NOT NULL CHECK (
        status_kind IN ('normal', 'provisional', 'estimated', 'forecast', 'break', 'missing', 'unknown', 'withdrawn')
    ),
    confidentiality_raw text,
    source_attributes jsonb NOT NULL CHECK (jsonb_typeof(source_attributes) = 'object'),
    observation_sha256 text NOT NULL CHECK (observation_sha256 ~ '^[0-9a-f]{64}$'),
    provider_valid_from timestamptz,
    provider_valid_to timestamptz,
    provider_action text,
    created_capture_id uuid NOT NULL,
    supersedes_revision_id bigint,
    UNIQUE (revision_id, indicator_id),
    UNIQUE (revision_id, indicator_id, period_raw),
    FOREIGN KEY (created_capture_id, indicator_id) REFERENCES macro_data.capture (capture_id, indicator_id),
    FOREIGN KEY (supersedes_revision_id, indicator_id, period_raw)
        REFERENCES macro_data.observation_revision (revision_id, indicator_id, period_raw),
    CHECK (isfinite(period_start) AND isfinite(period_end) AND period_start <= period_end),
    CHECK (effective_date IS NULL OR (effective_date = period_start AND effective_date = period_end)),
    CHECK ((value_decimal IS NULL) = (value_raw IS NULL)),
    CHECK ((value_decimal IS NULL) = (status_kind IN ('missing', 'withdrawn'))),
    CHECK ((provider_valid_from IS NULL) = (provider_action IS NULL)),
    CHECK (provider_action IS NULL OR provider_action IN ('Replace', 'Append', 'Delete')),
    CHECK (provider_valid_from IS NULL OR isfinite(provider_valid_from)),
    CHECK (provider_valid_to IS NULL OR (isfinite(provider_valid_to) AND provider_valid_from IS NOT NULL
                                        AND provider_valid_from < provider_valid_to))
);

CREATE TABLE macro_data.revision_capture (
    revision_id bigint NOT NULL,
    capture_id uuid NOT NULL,
    indicator_id text NOT NULL,
    PRIMARY KEY (revision_id, capture_id),
    FOREIGN KEY (revision_id, indicator_id) REFERENCES macro_data.observation_revision (revision_id, indicator_id),
    FOREIGN KEY (capture_id, indicator_id) REFERENCES macro_data.capture (capture_id, indicator_id)
);

CREATE INDEX macro_capture_series_time ON macro_data.capture (indicator_id, captured_at DESC);
CREATE INDEX macro_revision_period ON macro_data.observation_revision (indicator_id, period_raw, revision_id DESC);
CREATE INDEX macro_revision_effective ON macro_data.observation_revision (indicator_id, effective_date)
    WHERE effective_date IS NOT NULL;
CREATE UNIQUE INDEX macro_provider_version ON macro_data.observation_revision (
    indicator_id, period_raw, provider_valid_from, provider_action, observation_sha256
) WHERE provider_valid_from IS NOT NULL;

ALTER TABLE macro_data.series ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_data.schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_data.capture ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_data.observation_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_data.revision_capture ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON SCHEMA macro_data FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA macro_data FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA macro_data FROM PUBLIC;

DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role']
    LOOP
        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA macro_data FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA macro_data FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA macro_data FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
