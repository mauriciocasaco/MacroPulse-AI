# Market Data PostgreSQL persistence

The executable Supabase migration is
`supabase/migrations/202611070001_market_data.sql`. It creates the dedicated
`market_data` schema, five operational tables, integrity
triggers and the six initial asset mappings. Catalog inserts preserve existing
rows and abort when a seeded identity conflicts.

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
daily rows by mapping and half-open date range. The repository currently allows
persistence only for BTC-USD because that is the only reviewed date-label
convention. Other seeded mappings stay available in the catalog but require
symbol-specific evidence before their bars can be saved.

Tests requiring PostgreSQL use `MARKET_DATA_TEST_DSN`. They require a
disposable test database, create uniquely named schemas there, and retain those
schemas until that disposable database is removed. They never connect to a
live market data account. Without that variable, repository integration tests
are skipped.
