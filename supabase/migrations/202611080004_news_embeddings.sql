CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_extension e
        JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'vector' AND n.nspname = 'extensions'
    ) THEN
        RAISE EXCEPTION 'pgvector must be installed in the extensions schema for the news migration'
            USING HINT = 'Use a clean disposable database with pgvector available. This migration leaves existing extensions in place.';
    END IF;
END;
$$;

ALTER TABLE news.article_records
    ADD COLUMN is_synthetic boolean NOT NULL DEFAULT false,
    ADD COLUMN evidence_updated_at timestamptz CHECK (isfinite(evidence_updated_at));

CREATE FUNCTION news.mark_evidence_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    NEW.evidence_updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER news_evidence_update BEFORE UPDATE ON news.article_records
    FOR EACH ROW WHEN (OLD IS DISTINCT FROM NEW)
    EXECUTE FUNCTION news.mark_evidence_update();

CREATE TABLE news.embedding_models (
    id text CHECK (btrim(id) <> ''),
    revision text CHECK (btrim(revision) <> ''),
    dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    PRIMARY KEY (id, revision),
    UNIQUE (id, revision, dimensions)
);

CREATE TABLE news.record_embeddings (
    record_id bigint REFERENCES news.article_records (id) ON DELETE CASCADE,
    model_id text CHECK (btrim(model_id) <> ''),
    model_revision text CHECK (btrim(model_revision) <> ''),
    dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    embedding extensions.vector NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT clock_timestamp() CHECK (isfinite(embedded_at)),
    CHECK (extensions.vector_dims(embedding) = dimensions),
    CHECK (extensions.vector_norm(embedding) BETWEEN 0.99999 AND 1.00001),
    PRIMARY KEY (record_id, model_id, model_revision),
    FOREIGN KEY (model_id, model_revision, dimensions)
        REFERENCES news.embedding_models (id, revision, dimensions)
);
