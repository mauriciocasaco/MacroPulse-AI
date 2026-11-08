# Market Data PostgreSQL persistence

The initial Supabase migration is
`supabase/migrations/202611070001_market_data.sql`. It creates the dedicated
`market_data` schema, five operational tables, integrity
triggers and the six initial asset mappings. Catalog inserts preserve existing
rows and abort when a seeded identity conflicts.

`supabase/migrations/202611080001_yahoo_daily_conventions.sql` updates the
conventions for GC=F, CL=F, ^GSPC, VT and VWO. It checks the provider, symbol
and asset identity, accepts the original `unknown` value or the expected
final convention, and fails on missing or conflicting mappings. It leaves
Bitcoin, sign policies, and futures rolling metadata intact.

`backend.market_data.migrations.apply_migrations(dsn)` is the supported
application entry point. The checked-in SQL defines the schema objects and
catalog seed. The Python runner creates `market_data.schema_migrations`, then
applies the allowlisted SQL and records its version and SHA-256 checksum in
that ledger in the same transaction. Running the SQL file by itself does not
record a migration version. The runner rejects a previously recorded version
if its checksum has changed and reports the catalog counts. For Supabase,
pass the existing DSN through
`backend.market_data.connection.secure_supabase_dsn(dsn)` before supplying it
to `apply_migrations` or `PostgresPriceRepository`. This helper adds
`sslmode="verify-full"`, `gssencmode="disable"`, and the repository-local
`Certificate/prod-ca-2021.crt` path. Keep generic or disposable PostgreSQL DSNs
unchanged. Keep credentials in memory and out of command transcripts and
repository files. Before applying to any connected database, inspect its
identity and confirm it is the authorized development destination using an
independent project reference, such as the development Dashboard. Compare that
reference with the direct database host or the username when using a shared
pooler. A hostname that passes TLS verification alone does not identify the
Supabase project.

`PostgresPriceRepository` resolves an `AssetSource` to a database mapping ID,
starts an ingestion run, persists a normalized batch transactionally and reads
daily rows by mapping and half-open date range. With both migrations applied,
it accepts all six reviewed Yahoo mappings. The asset, provider, symbol and
date convention must agree with the Python catalog, and bars must use the
daily interval. Futures keep the date published by Yahoo and rolling
`unknown`. Equity series keep the New York session date, and Bitcoin keeps
the UTC date, including weekends. Official trade, settlement, and availability
times remain unset.

The unique bar key is `(asset_source_id, interval, bar_date, ohlc_basis)`.
Identical observations preserve content provenance and may advance
`last_seen_at` and `last_seen_run_id`. A different observation that shares
the retrieval time of the stored content or its latest sighting is rejected
with `equal_timestamp_conflict`. The stored bar and its provenance are
preserved. A run ID orders sightings of identical content and cannot resolve
a content conflict. Older observations are stale. A later correction replaces
the full observation, including optional values that become NULL. NULL volume
remains distinct from zero, and adjusted close stays separate from close.

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

## Historical importer

Run the following in PowerShell from the repository root. Dry-run is the
default. The initial dataset flag checks the reviewed half-open window, six
per-symbol counts, unique daily dates and exact inventory of six rejections.
Omit that flag for other backfills.

```powershell
python -m backend.market_data.historical_import --input-dir .\data\backfills\normalized\2019-11-08_2026-11-08 --archive-dir .\data\backfills --initial-dataset --dry-run
```

The importer reads version 1 gzip JSON exports and their referenced raw
archives. The export stores the normalized values as decimal strings or JSON
null, the original UTC retrieval time, source identity, batch window, and
raw positions and labels. The SHA-256 is for the compressed raw archive.
The importer checks it against the same bytes it reads, reruns the existing
normalizer locally and compares every record and its provenance. The hash
checks consistency between paired files. Authenticity depends on retaining
the reviewed archives and exports. The dry-run reports bounds and counts per
asset, retrieval metadata, raw hashes and each archived rejection.

For a later write to an authorized, migrated local database, supply
`MARKET_DATA_IMPORT_DSN` in the process environment and replace
`--dry-run` with `--apply-local`. The DSN needs one literal `127.0.0.1` host,
an explicit port, database and user. Use a verified local server and port.
Exclude tunnels or forwarded ports to external servers. The CLI accepts
only loopback for `--apply-local`.

Validation completes for the whole input before any write. Each archived
batch gets its own ingestion run and transaction. A failed batch rolls
back its bars and earlier committed batches remain. After resolving the
cause, replay the same inputs. Identical content reconciles as unchanged
or stale according to the repository policy, and the new runs record the
replay. Run summaries include insertions, unchanged observations,
corrections, stale observations and rejections. An unexpected rejection
stops the import after the repository records that batch's result. A hard
kill or connection loss can leave a run marked `running`. Replaying uses
new runs and preserves prior run records for inspection.

Run diagnostics include the archive path and hash and the position, raw
label, local date and code of each withheld observation. The repository
keeps the current bar and run summaries. Retain the archives to reconstruct
source metadata and superseded values.

## First Supabase DEV load

Run from the repository root in PowerShell with the project Python
environment active. The DEV modes read `MARKETPULSE_DEV_PROJECT_REF` and
`DATABASE_URL` from the local `.env`. Confirm the project in the DEV Dashboard
before the window. The PostgreSQL hostname or shared pooler username must
identify the pinned DEV project. The HTTPS project URL is separate. The
configured Supabase CA must be available locally, and every connection
uses `verify-full`.

```powershell
$devProject = 'swpithkjviavycrxpwed'
$archiveArgs = @(
    '--input-dir', '.\data\backfills\normalized\2019-11-08_2026-11-08',
    '--archive-dir', '.\data\backfills',
    '--initial-dataset'
)
```

Keep other writers and schema changes paused for the migration and load
window. The preflight is a repeatable read-only snapshot. The loader commits
individual batches on separate connections, so the snapshot cannot exclude
outside writes after preflight. Stop on a nonzero exit code and review
committed runs before retrying.

1. After authorization for the incremental migration, run this command.
   It requires the audited initial schema, its exact ledger entry, the
   initial catalog and empty ingestion tables. The runner locks its ledger,
   requires the incremental to be the sole pending version, checks the SQL
   hashes, and records the incremental in the same transaction as its SQL.
   The command then runs the read-only preflight again to verify both
   versions, checksums and the updated catalog.

   ```powershell
   python -m backend.market_data.historical_import @archiveArgs --migrate-dev-incremental --confirm-dev-write $devProject
   ```

2. Run the read-only destination preflight and the final offline dry-run.
   The preflight compares PostgreSQL 17 table definitions against the
   audited DDL and checks existing bars and completed runs against the
   archives. The dry-run validates the local inputs without reading the DSN.

   ```powershell
   python -m backend.market_data.historical_import @archiveArgs --preflight-dev
   python -m backend.market_data.historical_import @archiveArgs --dry-run
   ```

3. After explicit authorization for the historical load, run the following.
   The write mode repeats preflight before opening any ingestion run and
   checks the complete result after the final batch.

   ```powershell
   python -m backend.market_data.historical_import @archiveArgs --apply-dev --confirm-dev-write $devProject
   python -m backend.market_data.historical_import @archiveArgs --preflight-dev --require-complete
   ```

   Expect 1,761 GC=F, 1,760 CL=F, 1,758 each for ^GSPC, VT and VWO, and
   2,557 BTC-USD bars. The run summaries account for the six source
   rejections separately.

4. After authorization to replay, run the same load again and repeat the
   complete check. Expect zero inserts, corrections and stale observations,
   11,352 unchanged observations, six archived rejections and the same bar
   counts. The replay records new ingestion runs and updates last-seen run
   provenance.

   ```powershell
   python -m backend.market_data.historical_import @archiveArgs --apply-dev --confirm-dev-write $devProject
   python -m backend.market_data.historical_import @archiveArgs --preflight-dev --require-complete
   ```

5. After failure or interruption, inspect the current per-symbol counts,
   status totals and reported run IDs before reusing the load command.
   A matching subset of stored bars is valid for resume. Unexpected
   overlaps or provenance discrepancies block the load.

   ```powershell
   python -m backend.market_data.historical_import @archiveArgs --preflight-dev
   ```

Tests requiring PostgreSQL use `MARKET_DATA_TEST_DSN`. They require a
disposable test database, create uniquely named schemas there, and retain those
schemas until that disposable database is removed. The DEV workflow test
also requires permission to create and remove its own uniquely named
database on that loopback server. Without that variable, PostgreSQL integration
tests are skipped.
