# Market data in PostgreSQL

## Schema and migration runner

`supabase/migrations/202611070001_market_data.sql` creates the
`market_data` schema, catalog, ingestion runs, daily bars and integrity
triggers. `202611080001_yahoo_daily_conventions.sql` updates the verified
Yahoo date conventions for GC=F, CL=F, ^GSPC, VT and VWO. It preserves the
Bitcoin mapping, sign policies and futures rolling metadata.

The application entry point is
`backend.market_data.migrations.apply_migrations(dsn)`. The runner
applies allowlisted SQL in transactions, records each version and SHA-256
in `schema_migrations`, and rejects checksum drift. Executing a SQL file
directly bypasses the ledger.

Each batch persists atomically with its run counters. Identical content
preserves the original run and can advance the last-seen run. Older
observations count as stale. A different value with the same retrieval
timestamp is rejected as an equal-time conflict. A newer correction
replaces the current observation, including optional fields that became
null. See the [daily contract](market-data-foundation.md#daily-ohlcv-and-provenance)
for the financial and temporal invariants.

## Daily read queries

All queries are available through `PostgresPriceRepository`. Use the
`asset_source_id` from the catalog query, or resolve an existing
`AssetSource` with `resolve_mapping_id`. Mapping IDs identify both the
instrument and its provider. The read methods bind values as SQL parameters.

| Query | Python call | Result |
| --- | --- | --- |
| Available assets | `list_available_assets()` | One `AvailableAsset` per enabled mapping. It includes the asset ID and name, mapping ID, provider ID and name, provider symbol, economic unit, calendar, first and last stored dates, and bar count. |
| Full history | `read_daily(mapping_id)` | Daily `PriceBar1d` records ordered by ascending date. |
| Date range | `read_daily(mapping_id, window)` | Daily records with `start <= bar_date < end_exclusive`, ordered by ascending date. |
| Latest observations | `read_latest_daily(mapping_id, limit)` | The last 1 to 1,000 available observations, returned in ascending date order. The limit counts stored bars across calendar gaps. |
| Two instruments | `compare_daily(first_mapping_id, second_mapping_id, window)` | One `ComparisonPoint` per observed close, ordered by date then mapping ID. Each point includes the asset ID, mapping ID, provider ID, symbol, economic unit, date and close. |
| Chart data | `read_daily(mapping_id, window)` | The same `PriceBar1d` structure includes date, separate OHLC and adjusted close, volume, asset and provider identity, UTC retrieval time, source convention, value bases, quality and session status, adapter and provider versions, rolling method and volume unit. |

The schema represents enabled mappings through the `verified:` prefix on
`source_convention`. A verified mapping without bars has count zero and
two null coverage dates. All results are tuples. Reads for a valid unknown
mapping ID or a range with no observations return an empty tuple. A
comparison retains any observations from the known side. Mapping IDs must
be positive PostgreSQL bigints, and comparisons require distinct IDs.
`DateRange` takes Python `date` values with start strictly before end.

```python
from datetime import date

from backend.market_data import DateRange

# repository is an already configured PostgresPriceRepository.
assets = {(row.source_id, row.symbol): row for row in repository.list_available_assets()}
wti = assets["yahoo_finance", "CL=F"].asset_source_id
btc = assets["yahoo_finance", "BTC-USD"].asset_source_id
window = DateRange(date(2020, 4, 17), date(2020, 4, 22))
history = repository.read_daily(wti)
chart = repository.read_daily(wti, window)
latest = repository.read_latest_daily(wti, 20)
comparison = repository.compare_daily(wti, btc, window)
```

Dates are provider labels as described above. Comparisons keep each
instrument's observed dates and native units. CL=F uses USD per barrel,
GC=F USD per troy ounce, ^GSPC index points, VT and VWO USD per share,
and BTC-USD USD per BTC. The result includes no interpolation,
forward-fill, currency conversion or base-100 normalization. A date in
both series can represent different observation times. `retrieved_at`
records when the provider value was retrieved. It gives no historical
availability guarantee for point-in-time backtests.

## Phase 2.6 DEV audit

The following command uses the existing DEV connection guard and verifies
the initial archives locally before opening a repeatable-read, read-only
transaction. It checks the exact migration checksums, DDL and catalog, bar
content and provenance, per-run reconciliation, archive diagnostics and
the negative WTI observation. The historical window is
`[2019-11-08, 2026-11-08)`.

```powershell
python -m backend.market_data.dev_audit --input-dir ./data/backfills/normalized/2019-11-08_2026-11-08 --archive-dir ./data/backfills
```

The DEV snapshot at 2026-11-08 23:07:14 UTC contained 11,352 bars and 12
completed runs (six succeeded and six partial). All six series start
2019-11-08. BTC-USD has 2,557 bars through 2026-11-07. GC=F has 1,761,
CL=F has 1,760, and ^GSPC, VT and VWO each have 1,758 through 2026-11-06.
Each pass received 11,358 observations and recorded the same six source
rejections. The first pass inserted 11,352 bars and the replay recorded
11,352 unchanged, with zero corrections or stale observations in either
pass. All bars matched their verified archives and linked the original
run to the matching replay. All reported integrity counters were zero.
Adjusted close was present on all bars and differed from close on 1,722
observations each for VT and VWO. WTI retained a close of about
−37.63 USD/bbl on 2020-04-20.

The B-tree primary key on `price_bars` starts with
`(asset_source_id, interval, bar_date)`. On DEV, `EXPLAIN` without
`ANALYZE` used that key for the bar-access portion of the BTC full
history and date-range queries, and scanned it backward under `LIMIT`
for the 20 latest WTI observations. The BTC/WTI range probe used the same
key and sorted an estimated 55 rows by date and mapping. These are
planner estimates for the bar filters and ordering, without join or
end-to-end latency measurements. The run and last-seen indexes support
provenance lookups. The observed plans support keeping the existing
indexes at the current dataset size.

## Historical import

Archive inputs are local. Use `python -m backend.market_data.backfill --help`
for acquisition options. When the raw gzip files already exist, use
`python -m backend.market_data.backfill --offline` to regenerate normalized
exports from those archives. Offline mode leaves the raw files intact.

Validate the reviewed dataset from the repository root. The default import
mode is dry-run. The initial-dataset flag also checks the reviewed
half-open window, counts, and six rejected observations.

```powershell
python -m backend.market_data.historical_import --input-dir ./data/backfills/normalized/2019-11-08_2026-11-08 --archive-dir ./data/backfills --initial-dataset --dry-run
```

The importer verifies each raw archive hash, reruns the normalizer, and
compares the resulting values, provenance and rejections with the export.
A matching hash checks file consistency. Authenticity depends on retaining
the reviewed archive set. Omit `--initial-dataset` for other backfills.
To write to a migrated, authorized local database, set
`MARKET_DATA_IMPORT_DSN` to a verified loopback server at `127.0.0.1` with
explicit port, database and user, and use `--apply-local`.

Validation precedes writes. A persistence failure rolls back the current
batch while earlier committed batches remain. Inspect run status and
counts, resolve the failure, then replay the same validated inputs if
authorized. Replays record new runs. Repeated identical content preserves
bar uniqueness and original provenance. An interrupted run can remain
marked `running` and needs review before retrying.

### Supabase DEV controls

The read-only audit and importer DEV modes use the existing local
configuration. Keep `MARKETPULSE_DEV_PROJECT_REF` and `DATABASE_URL` in the
ignored `.env`. The client matches the configured project to the direct
database hostname or the pooler username, verifies TLS with the local CA
at `Certificate/prod-ca-2021.crt`, and rejects ambiguous routing options.
The DEV project reference is an identifier. Database credentials and the
local CA file stay outside the repository.

Use `--preflight-dev` to verify the pinned destination, schema, ledger,
catalog and existing data without writes. Add `--require-complete` to
require all expected bars. The separate `dev_audit` command above also
checks the original load and replay counters and the provenance links.
The original DEV migration and write modes require explicit authorization
and `--confirm-dev-write` with the pinned project reference. Inspect the
ledger and run status before any authorized write. The preflight snapshot
does not lock out other writers after it ends.

## Local tests

Install the pinned development requirements from the
[README](../README.md#run-the-tests-locally). From the repository root in
PowerShell 7.4 or newer with Docker running, copy the whole block below.
It starts disposable PostgreSQL 17 on loopback, configures a temporary
TLS certificate and runs the full suite with both test DSNs set.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $PSNativeCommandUseErrorActionPreference = $true
    $marketPulseTestId = [guid]::NewGuid().ToString('N')
    $marketPulseTestContainer = "marketpulse-test-pg-$marketPulseTestId"
    $marketPulseTestCa = Join-Path ([System.IO.Path]::GetTempPath()) "$marketPulseTestContainer.crt"
    $marketPulsePreviousDataDsn = $env:MARKET_DATA_TEST_DSN
    $marketPulsePreviousApiDsn = $env:MARKET_API_TEST_DSN
    $marketPulseStarted = $false
    try {
        docker run --rm -d --name $marketPulseTestContainer -e POSTGRES_HOST_AUTH_METHOD=trust --publish 127.0.0.1::5432 postgres:17
        $marketPulseStarted = $true
        docker exec $marketPulseTestContainer sh -ec 'for attempt in $(seq 1 30); do if pg_isready -h 127.0.0.1 -U postgres; then exit 0; fi; sleep 1; done; exit 1'

        docker exec -u postgres $marketPulseTestContainer openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 -subj /CN=localhost -addext subjectAltName=IP:127.0.0.1 -keyout /tmp/mp-test.key -out /tmp/mp-test.crt
        docker exec -u postgres $marketPulseTestContainer chmod 600 /tmp/mp-test.key
        docker exec $marketPulseTestContainer psql -U postgres -v ON_ERROR_STOP=1 `
            -c "ALTER SYSTEM SET ssl_cert_file = '/tmp/mp-test.crt'" `
            -c "ALTER SYSTEM SET ssl_key_file = '/tmp/mp-test.key'" `
            -c "ALTER SYSTEM SET ssl = 'on'" -c 'SELECT pg_reload_conf()'
        docker exec $marketPulseTestContainer sh -ec 'for attempt in $(seq 1 10); do if psql "host=127.0.0.1 user=postgres sslmode=require" -Atqc "SHOW ssl" 2>/dev/null | grep -qx on; then exit 0; fi; sleep 1; done; exit 1'
        docker cp "${marketPulseTestContainer}:/tmp/mp-test.crt" $marketPulseTestCa

        $marketPulseTestPort = (docker port $marketPulseTestContainer 5432/tcp) -replace '^127\.0\.0\.1:', ''
        if ($marketPulseTestPort -notmatch '^\d+$') { throw 'Local PostgreSQL port unavailable' }
        $marketPulseCaDsn = $marketPulseTestCa.Replace('\', '/').Replace("'", "\'")
        $marketPulseTestBaseDsn = "host=127.0.0.1 port=$marketPulseTestPort dbname=postgres user=postgres"
        $env:MARKET_DATA_TEST_DSN = "$marketPulseTestBaseDsn sslmode=disable"
        $env:MARKET_API_TEST_DSN = "$marketPulseTestBaseDsn sslmode=verify-full sslrootcert='$marketPulseCaDsn'"
        .\.venv\Scripts\python.exe -m pytest -ra
    } finally {
        $marketPulseExitCode = $LASTEXITCODE
        $env:MARKET_DATA_TEST_DSN = $marketPulsePreviousDataDsn
        $env:MARKET_API_TEST_DSN = $marketPulsePreviousApiDsn
        try {
            if (Test-Path -LiteralPath $marketPulseTestCa) { Remove-Item -LiteralPath $marketPulseTestCa }
        } finally {
            if ($marketPulseStarted) { docker stop $marketPulseTestContainer }
            if ($marketPulseExitCode -ne 0) { $global:LASTEXITCODE = $marketPulseExitCode }
        }
    }
}
```

The fixtures use synthetic data and create schemas and temporary databases.
Market data and news integration tests require `MARKET_DATA_TEST_DSN`. The
FastAPI TLS tests require `MARKET_API_TEST_DSN`. The workflow restores prior
environment values and removes the disposable instance and certificate.
