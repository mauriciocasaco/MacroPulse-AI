CREATE SCHEMA news;
REVOKE ALL ON SCHEMA news FROM PUBLIC;

CREATE TABLE news.sources (
    id text PRIMARY KEY CHECK (id ~ '^[a-z][a-z0-9_]*$'),
    name text NOT NULL CHECK (btrim(name) <> ''),
    terms_url text CHECK (terms_url ~ '^https?://[^/[:space:]]+[^[:space:]]*$')
);

CREATE TABLE news.articles (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_url text NOT NULL UNIQUE CHECK (canonical_url ~ '^https?://[^/[:space:]]+[^[:space:]]*$'),
    publisher text NOT NULL CHECK (btrim(publisher) <> ''),
    title text CHECK (btrim(title) <> ''),
    published_at timestamptz CHECK (isfinite(published_at)),
    published_on date CHECK (isfinite(published_on)),
    CHECK (published_at IS NULL OR published_on IS NULL)
);

CREATE TABLE news.article_records (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    article_id bigint NOT NULL REFERENCES news.articles (id),
    source_id text NOT NULL REFERENCES news.sources (id),
    external_id text CHECK (btrim(external_id) <> ''),
    source_url text NOT NULL CHECK (source_url ~ '^https?://[^/[:space:]]+[^[:space:]]*$'),
    retrieved_at timestamptz NOT NULL CHECK (isfinite(retrieved_at)),
    provider_observed_at timestamptz CHECK (isfinite(provider_observed_at) AND provider_observed_at <= retrieved_at),
    reported_published_at timestamptz CHECK (isfinite(reported_published_at) AND reported_published_at <= retrieved_at),
    reported_published_on date CHECK (isfinite(reported_published_on)),
    provenance_ref text NOT NULL CHECK (btrim(provenance_ref) <> ''),
    content_kind text NOT NULL DEFAULT 'none' CHECK (content_kind IN ('none', 'excerpt', 'full')),
    content text CHECK (btrim(content) <> ''),
    storage_permission text NOT NULL DEFAULT 'metadata_only'
        CHECK (storage_permission IN ('metadata_only', 'excerpt', 'full')),
    public_display_allowed boolean NOT NULL DEFAULT false,
    embedding_allowed boolean NOT NULL DEFAULT false,
    rights_basis text CHECK (btrim(rights_basis) <> ''),
    UNIQUE (source_id, external_id),
    CHECK (reported_published_at IS NULL OR reported_published_on IS NULL),
    CHECK ((content_kind = 'none') = (content IS NULL)),
    CHECK (content_kind = 'none' OR storage_permission = 'full' OR content_kind = storage_permission),
    CHECK (rights_basis IS NOT NULL OR (
        storage_permission = 'metadata_only' AND NOT public_display_allowed AND NOT embedding_allowed
    ))
);

CREATE TABLE news.article_assets (
    article_id bigint REFERENCES news.articles (id),
    asset_id text REFERENCES market_data.assets (id),
    evidence text NOT NULL CHECK (btrim(evidence) <> ''),
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp() CHECK (isfinite(linked_at)),
    PRIMARY KEY (article_id, asset_id)
);

CREATE INDEX news_articles_published_at_idx ON news.articles (published_at, id) WHERE published_at IS NOT NULL;
CREATE INDEX news_article_records_article_idx ON news.article_records (article_id);
CREATE UNIQUE INDEX news_article_records_source_url_idx ON news.article_records (source_id, source_url)
    WHERE external_id IS NULL;
CREATE INDEX news_article_assets_asset_idx ON news.article_assets (asset_id, article_id);
