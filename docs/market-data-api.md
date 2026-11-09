# Market data HTTP API

From the repository root, install `backend/requirements.txt` with Python 3.12.

## Local Supabase DEV startup

In PowerShell, start FastAPI with an explicit loopback host and port. The local
dotenv path is limited to this launch form and direct loopback requests. It also
accepts requests from the loopback Vite proxy.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8001
```

In a second terminal, check liveness and catalog access separately.

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8001/assets | Select-Object symbol, name, first_date, last_date, bar_count
```

The backend chooses the credential in this order.

1. Explicit process `MARKET_API_DSN`.
2. The seven read-only settings below, with process values overriding matching
   keys in the ignored `.env` file. A partial or invalid set fails closed.
3. For legacy configurations, `MARKET_API_DSN` in the optional, ignored
   `.env.market-api` file, then in the ignored `.env` if that separate file is absent.

The API never falls back to `DATABASE_URL`.

If `.env.market-api` exists, it must contain `MARKET_API_DSN`. For the DEV project
reference, the process value takes precedence over `.env.market-api`, then `.env`.
Both files are read from the repository root without variable interpolation or
exporting values to the process. Set `MARKETPULSE_DISABLE_LOCAL_DOTENV=1` to
disable dotenv loading, including when running the application in a deployed
environment. Process-provided DSNs for other PostgreSQL destinations keep the
explicit configuration path and require a trusted CA with hostname verification.

The DEV DSN must identify the authorized project using its direct database hostname
or an approved Supavisor pooler hostname and a project-qualified username. Provide
the explicit port and `postgres` database. The backend pins the repository's
trusted CA in `Certificate/prod-ca-2021.crt`, forces `sslmode=verify-full` and
disallows libpq routing overrides for DEV. A URI DSN needs percent-encoding for
reserved characters in the password.

### Dedicated DEV role

Use the standalone PostgreSQL login `marketpulse_api_ro` with no role memberships, elevated attributes,
object ownership, database or schema creation rights, effective table or column
write grants, `SELECT` grant options, sequence mutation privileges, or callable
`SECURITY DEFINER` routines.
An authorized DEV administrator must provision its credential and grant these permissions.

| Scope | Required permissions |
| --- | --- |
| Database `postgres` | `CONNECT` |
| Schemas | `USAGE` on `market_data`, `news`, and `extensions` |
| Market tables | `SELECT` on `assets`, `asset_sources`, `data_sources`, `ingestion_runs`, and `price_bars` in `market_data` |
| Evidence tables | `SELECT` on `sources`, `articles`, `article_records`, `record_assets`, and `record_embeddings` in `news` |
| Vector type | `USAGE` on `extensions.vector`, normally granted through `PUBLIC` |

Configure the dedicated credential in the existing ignored `.env` file.

| Variable | Purpose |
| --- | --- |
| `USER_RO` | `marketpulse_api_ro` on the direct endpoint, or `marketpulse_api_ro.<project-ref>` on the pooler |
| `PASS_USER_RO` | Role password as a dotenv value, without URI percent-encoding |
| `HOST_RO` and `PORT_RO` | Actual DEV endpoint and explicit port |
| `DB_RO` | `postgres` |
| `SSL_MODE_RO` | `verify-full` |
| `SSL_ROOT_CERT_RO` | Absolute path to the pinned CA or `Certificate/prod-ca-2021.crt` relative to the repository |
| `MARKETPULSE_DEV_PROJECT_REF` | Authorized DEV project reference |

The backend builds the connection string in memory. To rotate the password,
use the authorized DEV admin connection and psql's interactive
`\password marketpulse_api_ro` command. Update `PASS_USER_RO` locally and
restart and verify the API. If `.env` is lost, recover the endpoint and project
reference from DEV, locate the trusted CA, and rotate the dedicated password if
it cannot be recovered from an authorized secret store.

The analysis query resolves the vector type and cosine operator in `extensions`.
That schema also exposes the standard `pg_stat_statements` statistics views in
DEV. The role has no `pg_read_all_stats` membership, so the views hide other
users' query text and query IDs. `TEMPORARY` from `PUBLIC` allows session-local
tables and resource use. PostgreSQL also exposes large-object creation through
`PUBLIC`. A client using this credential outside the API can create its own
persistent large objects in a read-write transaction. The API always starts a
read-only transaction and checks effective privileges on each DEV connection.
The existing DEV role can also read `market_data.schema_migrations`,
`news.article_assets` and `news.ingestion_batches`. Those tables are outside the
API's required grants above.

For every DEV connection, the API checks identity and effective privileges in a
read-only transaction before it queries market or news data. `/health` reports
process liveness and can succeed while `/assets` returns a generic `503`. The local
server logs fixed reason codes such as `dev_credential_not_read_only`,
`dev_credential_missing_select`, and `dev_route_or_tls_config_invalid`.

```powershell
# Frontend terminal, from frontend/
$env:MARKETPULSE_API_PORT = '8001'
npm.cmd run dev
```

The running service provides Swagger UI at `/docs` and its contract at `/openapi.json`.
Use the `asset_source_id` returned by `/assets` to identify an asset and its source
within this database. The catalog also exposes the logical asset ID, symbol,
source name, economic unit and calendar.

| Route | Behavior |
| --- | --- |
| `GET /health` | Process liveness, independent of database connectivity |
| `GET /assets` | Verified mappings and daily coverage, including mappings with zero bars |
| `GET /assets/{asset_source_id}/history` | Stored daily history in ascending order |
| `GET /assets/{asset_source_id}/history?start=YYYY-MM-DD&end_exclusive=YYYY-MM-DD` | History over a half-open calendar date range |
| `GET /assets/{asset_source_id}/latest?limit=30` | Latest stored observations in ascending order, with a limit of 1–1,000 |
| `GET /compare?first=1&second=2&start=YYYY-MM-DD&end_exclusive=YYYY-MM-DD` | Raw and provider-adjusted closes for two distinct mappings in a half-open range |

Valid mappings without observations return an empty list with HTTP `200`.
An unknown or unverified mapping returns `404`. Invalid parameters return `422`.
The comparison preserves original units and each asset's observed dates, including
weekends where applicable. The response uses JSON strings for decimal values to
preserve their precision. `close` is the provider's unadjusted close and
`adjusted_close` is the separately reported value, or `null` when unavailable.
`retrieved_at` records when the stored observation was obtained. The repository
does not persist independent exchange availability, settlement time or exchange
trade date, so history exposes those fields as `null`. History and comparison
represent the current stored data and do not reconstruct past revisions. Catalog
counts and bars come from separate reads and can reflect concurrent imports.

## Local verification

The unit tests use synthetic repository responses. The
[full local test workflow](market-data-postgres.md#local-tests) starts disposable
PostgreSQL 17 with verified TLS and sets `MARKET_API_TEST_DSN` for the integration
tests. Those tests create a temporary database, exercise the HTTP endpoints,
verify that PostgreSQL rejects writes and verify that a hostname mismatch
returns a generic error. They skip when `MARKET_API_TEST_DSN` is unset.
