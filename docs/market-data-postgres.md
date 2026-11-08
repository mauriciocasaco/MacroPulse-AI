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
