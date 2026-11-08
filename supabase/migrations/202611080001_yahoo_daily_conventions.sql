DO $migration$
DECLARE
    expected record;
    mapping_id bigint;
    actual_asset_id text;
    actual_convention text;
BEGIN
    FOR expected IN
        SELECT * FROM (VALUES
            ('gold_future', 'GC=F', 'verified:yahoo_futures_published_date:v1'),
            ('wti_future', 'CL=F', 'verified:yahoo_futures_published_date:v1'),
            ('sp500_index', '^GSPC', 'verified:yahoo_us_equity_open_date:v1'),
            ('world_equity_etf', 'VT', 'verified:yahoo_us_equity_open_date:v1'),
            ('emerging_markets_etf', 'VWO', 'verified:yahoo_us_equity_open_date:v1')
        ) AS mappings(asset_id, symbol, convention)
    LOOP
        SELECT id, asset_id, source_convention
          INTO mapping_id, actual_asset_id, actual_convention
          FROM market_data.asset_sources
         WHERE source_id = 'yahoo_finance' AND symbol = expected.symbol
         FOR UPDATE;

        IF NOT FOUND OR actual_asset_id IS DISTINCT FROM expected.asset_id THEN
            RAISE EXCEPTION 'unexpected Yahoo mapping identity for %', expected.symbol;
        END IF;

        IF actual_convention = 'unknown' THEN
            UPDATE market_data.asset_sources
               SET source_convention = expected.convention
             WHERE id = mapping_id;
        ELSIF actual_convention IS DISTINCT FROM expected.convention THEN
            RAISE EXCEPTION 'unexpected Yahoo mapping convention for %', expected.symbol;
        END IF;
    END LOOP;
END;
$migration$;
