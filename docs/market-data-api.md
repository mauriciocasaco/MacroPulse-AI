# Market data HTTP API

From the repository root, install `backend/requirements.txt` with Python 3.12.
Set `MARKET_API_DSN` through the process environment using a dedicated PostgreSQL
role with `CONNECT`, `USAGE` on `market_data` and `SELECT` on the price tables.
Use a TCP hostname covered by the server certificate and configure the trusted CA
through `sslrootcert` in the DSN or the standard libpq root certificate location.
The API forces TLS with hostname verification and opens PostgreSQL transactions
in read-only mode. Connection and database errors return a generic `503`.

```powershell
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
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
