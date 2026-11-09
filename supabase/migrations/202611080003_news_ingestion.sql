ALTER TABLE news.article_records
    ADD COLUMN themes text[] NOT NULL DEFAULT '{}',
    ADD COLUMN entities text[] NOT NULL DEFAULT '{}',
    ADD COLUMN reported_publisher text,
    ADD COLUMN reported_publication_raw text,
    ADD COLUMN event_at timestamptz CHECK (isfinite(event_at)),
    ADD COLUMN event_on date CHECK (isfinite(event_on)),
    ADD CHECK (event_at IS NULL OR event_on IS NULL);

CREATE TABLE news.record_assets (
    record_id bigint REFERENCES news.article_records (id) ON DELETE CASCADE,
    asset_id text REFERENCES market_data.assets (id),
    relationship text NOT NULL CHECK (relationship IN ('direct', 'thematic', 'macro')),
    confidence text NOT NULL CHECK (confidence IN ('medium', 'high')),
    evidence text NOT NULL CHECK (btrim(evidence) <> ''),
    method_version text NOT NULL CHECK (btrim(method_version) <> ''),
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp() CHECK (isfinite(linked_at)),
    PRIMARY KEY (record_id, asset_id)
);

CREATE INDEX news_record_assets_asset_idx ON news.record_assets (asset_id, record_id);

CREATE FUNCTION news.mark_link_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    NEW.linked_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER news_link_update BEFORE UPDATE ON news.record_assets
    FOR EACH ROW WHEN (OLD IS DISTINCT FROM NEW)
    EXECUTE FUNCTION news.mark_link_update();

CREATE TABLE news.ingestion_batches (
    source_id text REFERENCES news.sources (id),
    provenance_ref text CHECK (btrim(provenance_ref) <> ''),
    adapter_version text CHECK (btrim(adapter_version) <> ''),
    selection_sha256 text CHECK (selection_sha256 ~ '^[0-9a-f]{64}$'),
    archive_sha256 text NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    result jsonb NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT clock_timestamp() CHECK (isfinite(completed_at)),
    PRIMARY KEY (source_id, provenance_ref, adapter_version, selection_sha256)
);
