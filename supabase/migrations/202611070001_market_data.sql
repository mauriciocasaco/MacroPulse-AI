CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE market_data.assets (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    category text NOT NULL CHECK (length(btrim(category)) > 0),
    instrument_type text NOT NULL CHECK (length(btrim(instrument_type)) > 0),
    quote_currency text,
    economic_unit text NOT NULL CHECK (length(btrim(economic_unit)) > 0),
    calendar_code text NOT NULL CHECK (length(btrim(calendar_code)) > 0),
    sign_policy text NOT NULL CHECK (sign_policy IN ('strictly_positive', 'signed_allowed')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    CHECK (quote_currency IS NULL OR quote_currency ~ '^[A-Z]{3}$')
);

CREATE TABLE market_data.data_sources (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE TABLE market_data.asset_sources (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id text NOT NULL REFERENCES market_data.assets(id),
    source_id text NOT NULL REFERENCES market_data.data_sources(id),
    symbol text NOT NULL CHECK (length(btrim(symbol)) > 0),
    source_convention text NOT NULL CHECK (length(btrim(source_convention)) > 0),
    volume_unit text,
    rolling_method text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    UNIQUE (source_id, symbol)
);
CREATE INDEX asset_sources_asset_idx ON market_data.asset_sources(asset_id);

CREATE TABLE market_data.ingestion_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_source_id bigint NOT NULL REFERENCES market_data.asset_sources(id),
    interval text NOT NULL CHECK (interval = '1d'),
    start_date date NOT NULL,
    end_date_exclusive date NOT NULL,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'abandoned')),
    adapter_version text NOT NULL CHECK (length(btrim(adapter_version)) > 0),
    provider_version text,
    received_count bigint NOT NULL DEFAULT 0 CHECK (received_count >= 0),
    inserted_count bigint NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    unchanged_count bigint NOT NULL DEFAULT 0 CHECK (unchanged_count >= 0),
    corrected_count bigint NOT NULL DEFAULT 0 CHECK (corrected_count >= 0),
    stale_count bigint NOT NULL DEFAULT 0 CHECK (stale_count >= 0),
    rejected_count bigint NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    error_code text,
    diagnostics jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(diagnostics) = 'array'),
    CHECK (start_date < end_date_exclusive),
    CHECK ((status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL AND finished_at >= started_at)),
    CHECK ((status IN ('succeeded', 'partial') AND received_count = inserted_count + unchanged_count + corrected_count + stale_count + rejected_count)
        OR status IN ('running', 'failed', 'abandoned')),
    CHECK (status NOT IN ('succeeded', 'partial') OR ((status = 'partial') = (rejected_count > 0))),
    UNIQUE (id, asset_source_id, interval)
);
CREATE INDEX ingestion_runs_source_started_idx ON market_data.ingestion_runs(asset_source_id, started_at DESC);

CREATE TABLE market_data.price_bars (
    asset_source_id bigint NOT NULL REFERENCES market_data.asset_sources(id),
    interval text NOT NULL CHECK (interval = '1d'),
    bar_date date NOT NULL,
    ohlc_basis text NOT NULL CHECK (ohlc_basis = 'provider_unadjusted'),
    open numeric,
    high numeric,
    low numeric,
    close numeric NOT NULL,
    adjusted_close numeric,
    adjusted_close_basis text,
    volume numeric,
    retrieved_at timestamptz NOT NULL,
    source_convention text NOT NULL CHECK (length(btrim(source_convention)) > 0),
    session_status text NOT NULL CHECK (session_status IN ('closed', 'provisional', 'unknown')),
    quality_status text NOT NULL CHECK (quality_status IN ('complete', 'partial')),
    run_id bigint NOT NULL,
    last_seen_at timestamptz NOT NULL,
    last_seen_run_id bigint NOT NULL,
    PRIMARY KEY (asset_source_id, interval, bar_date, ohlc_basis),
    FOREIGN KEY (run_id, asset_source_id, interval) REFERENCES market_data.ingestion_runs(id, asset_source_id, interval),
    FOREIGN KEY (last_seen_run_id, asset_source_id, interval) REFERENCES market_data.ingestion_runs(id, asset_source_id, interval),
    CHECK ((adjusted_close IS NULL AND adjusted_close_basis IS NULL) OR (adjusted_close IS NOT NULL AND adjusted_close_basis IS NOT NULL AND adjusted_close_basis = 'provider_reported')),
    CHECK ((quality_status = 'complete' AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL)
        OR (quality_status = 'partial' AND (open IS NULL OR high IS NULL OR low IS NULL))),
    CHECK (high IS NULL OR low IS NULL OR high >= low),
    CHECK (high IS NULL OR open IS NULL OR high >= open),
    CHECK (high >= close),
    CHECK (low IS NULL OR open IS NULL OR low <= open),
    CHECK (low <= close),
    CHECK (volume IS NULL OR volume >= 0),
    CHECK (retrieved_at <= last_seen_at),
    CHECK ((last_seen_at, last_seen_run_id) >= (retrieved_at, run_id)),
    CHECK (open IS NULL OR open::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    CHECK (high IS NULL OR high::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    CHECK (low IS NULL OR low::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    CHECK (close::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    CHECK (adjusted_close IS NULL OR adjusted_close::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    CHECK (volume IS NULL OR volume::text NOT IN ('NaN', 'Infinity', '-Infinity'))
);
CREATE INDEX price_bars_run_idx ON market_data.price_bars(run_id, asset_source_id, interval);
CREATE INDEX price_bars_last_seen_run_idx ON market_data.price_bars(last_seen_run_id, asset_source_id, interval);

CREATE FUNCTION market_data.enforce_price_sign_policy() RETURNS trigger
LANGUAGE plpgsql AS $function$
DECLARE
    policy text;
BEGIN
    SELECT assets.sign_policy INTO STRICT policy
      FROM market_data.asset_sources AS mappings
      JOIN market_data.assets AS assets ON assets.id = mappings.asset_id
     WHERE mappings.id = NEW.asset_source_id
     FOR SHARE OF assets;
    IF policy = 'strictly_positive' AND (
        NEW.open <= 0 OR NEW.high <= 0 OR NEW.low <= 0 OR NEW.close <= 0 OR NEW.adjusted_close <= 0
    ) THEN
        RAISE EXCEPTION 'price violates asset sign policy' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER price_bars_sign_policy
BEFORE INSERT OR UPDATE OF open, high, low, close, adjusted_close, asset_source_id
ON market_data.price_bars FOR EACH ROW EXECUTE FUNCTION market_data.enforce_price_sign_policy();

CREATE FUNCTION market_data.protect_asset_sign_policy() RETURNS trigger
LANGUAGE plpgsql AS $function$
BEGIN
    IF NEW.sign_policy IS DISTINCT FROM OLD.sign_policy
       AND NEW.sign_policy = 'strictly_positive'
       AND EXISTS (
           SELECT 1 FROM market_data.asset_sources AS mappings
           JOIN market_data.price_bars AS bars ON bars.asset_source_id = mappings.id
           WHERE mappings.asset_id = NEW.id
             AND (bars.open <= 0 OR bars.high <= 0 OR bars.low <= 0 OR bars.close <= 0 OR bars.adjusted_close <= 0)
       ) THEN
        RAISE EXCEPTION 'existing bars violate requested asset sign policy' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER assets_sign_policy_guard
BEFORE UPDATE OF sign_policy ON market_data.assets
FOR EACH ROW EXECUTE FUNCTION market_data.protect_asset_sign_policy();

CREATE FUNCTION market_data.protect_asset_source_identity() RETURNS trigger
LANGUAGE plpgsql AS $function$
BEGIN
    IF (NEW.asset_id, NEW.source_id, NEW.symbol) IS DISTINCT FROM (OLD.asset_id, OLD.source_id, OLD.symbol) THEN
        RAISE EXCEPTION 'asset source identity is immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER asset_source_identity_guard
BEFORE UPDATE OF asset_id, source_id, symbol ON market_data.asset_sources
FOR EACH ROW EXECUTE FUNCTION market_data.protect_asset_source_identity();

DO $seed$
BEGIN
    IF EXISTS (
        SELECT 1 FROM (VALUES
            ('gold_future', 'Gold', 'commodities', 'future', 'USD', 'USD/troy_oz', 'gold_futures', 'strictly_positive'),
            ('wti_future', 'WTI crude oil', 'commodities', 'future', 'USD', 'USD/bbl', 'wti_futures', 'signed_allowed'),
            ('sp500_index', 'S&P 500', 'equity', 'index', NULL, 'index_points', 'us_equity', 'strictly_positive'),
            ('world_equity_etf', 'Global equity market', 'equity', 'etf', 'USD', 'USD/share', 'vt_sessions', 'strictly_positive'),
            ('emerging_markets_etf', 'Emerging markets', 'equity', 'etf', 'USD', 'USD/share', 'vwo_sessions', 'strictly_positive'),
            ('bitcoin_usd', 'Bitcoin', 'cryptoassets', 'cryptocurrency_spot', 'USD', 'USD/BTC', '24_7', 'strictly_positive')
        ) AS seed(id, name, category, instrument_type, quote_currency, economic_unit, calendar_code, sign_policy)
        JOIN market_data.assets AS actual USING (id)
        WHERE (actual.name, actual.category, actual.instrument_type, actual.quote_currency, actual.economic_unit, actual.calendar_code, actual.sign_policy)
          IS DISTINCT FROM (seed.name, seed.category, seed.instrument_type, seed.quote_currency, seed.economic_unit, seed.calendar_code, seed.sign_policy)
    ) THEN
        RAISE EXCEPTION 'existing market_data asset catalog conflicts with migration seed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM market_data.data_sources WHERE id = 'yahoo_finance' AND name <> 'Yahoo Finance'
    ) THEN
        RAISE EXCEPTION 'existing Yahoo Finance source catalog entry conflicts with migration seed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES
            ('gold_future', 'GC=F', 'unknown', 'unknown'),
            ('wti_future', 'CL=F', 'unknown', 'unknown'),
            ('sp500_index', '^GSPC', 'unknown', NULL),
            ('world_equity_etf', 'VT', 'unknown', NULL),
            ('emerging_markets_etf', 'VWO', 'unknown', NULL),
            ('bitcoin_usd', 'BTC-USD', 'verified:yahoo_bitcoin_utc_date:v1', NULL)
        ) AS seed(asset_id, symbol, source_convention, rolling_method)
        JOIN market_data.asset_sources AS actual ON actual.source_id = 'yahoo_finance' AND actual.symbol = seed.symbol
        WHERE (actual.asset_id, actual.source_convention, actual.rolling_method)
          IS DISTINCT FROM (seed.asset_id, seed.source_convention, seed.rolling_method)
    ) THEN
        RAISE EXCEPTION 'existing market_data source mapping conflicts with migration seed';
    END IF;
END;
$seed$;

INSERT INTO market_data.assets (id, name, category, instrument_type, quote_currency, economic_unit, calendar_code, sign_policy)
VALUES
    ('gold_future', 'Gold', 'commodities', 'future', 'USD', 'USD/troy_oz', 'gold_futures', 'strictly_positive'),
    ('wti_future', 'WTI crude oil', 'commodities', 'future', 'USD', 'USD/bbl', 'wti_futures', 'signed_allowed'),
    ('sp500_index', 'S&P 500', 'equity', 'index', NULL, 'index_points', 'us_equity', 'strictly_positive'),
    ('world_equity_etf', 'Global equity market', 'equity', 'etf', 'USD', 'USD/share', 'vt_sessions', 'strictly_positive'),
    ('emerging_markets_etf', 'Emerging markets', 'equity', 'etf', 'USD', 'USD/share', 'vwo_sessions', 'strictly_positive'),
    ('bitcoin_usd', 'Bitcoin', 'cryptoassets', 'cryptocurrency_spot', 'USD', 'USD/BTC', '24_7', 'strictly_positive')
ON CONFLICT (id) DO NOTHING;

INSERT INTO market_data.data_sources (id, name)
VALUES ('yahoo_finance', 'Yahoo Finance')
ON CONFLICT (id) DO NOTHING;

INSERT INTO market_data.asset_sources (asset_id, source_id, symbol, source_convention, rolling_method)
VALUES
    ('gold_future', 'yahoo_finance', 'GC=F', 'unknown', 'unknown'),
    ('wti_future', 'yahoo_finance', 'CL=F', 'unknown', 'unknown'),
    ('sp500_index', 'yahoo_finance', '^GSPC', 'unknown', NULL),
    ('world_equity_etf', 'yahoo_finance', 'VT', 'unknown', NULL),
    ('emerging_markets_etf', 'yahoo_finance', 'VWO', 'unknown', NULL),
    ('bitcoin_usd', 'yahoo_finance', 'BTC-USD', 'verified:yahoo_bitcoin_utc_date:v1', NULL)
ON CONFLICT (source_id, symbol) DO NOTHING;
