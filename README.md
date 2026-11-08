# MarketPulse AI

A Python and PostgreSQL foundation for evidence-based, multi-asset market
research. It acquires daily prices, validates financial and temporal
invariants, and stores observations with their source and ingestion history.

## What works today

- Daily Yahoo Finance OHLCV for gold and WTI futures, the S&P 500, two
  equity ETFs and Bitcoin.
- Reproducible import from hashed archives, with run counters, rejected-row
  diagnostics and idempotent persistence.
- Parameterized PostgreSQL reads for the asset catalog, full histories,
  date ranges, latest observations, chart data and two-asset comparisons.
- A read-only FastAPI service for the daily catalog and price observations,
  with OpenAPI documentation.
- A PostgreSQL migration for news metadata, source provenance and asset links,
  with a bounded historical source evaluation.
- A read-only Supabase DEV audit verified **11,352 bars across six assets**
  and 12 ingestion runs, with zero differences against the reviewed archives.
  See the [audit results](docs/market-data-postgres.md#phase-26-dev-audit).

## Run the tests locally

Use Python 3.12 from the repository root. In PowerShell, create a virtual
environment and install the development dependencies.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
```

Follow the [full local test workflow](docs/market-data-postgres.md#local-tests)
to run all tests with disposable PostgreSQL 17 and verified TLS for the API.
For tests that do not require PostgreSQL, run `.\.venv\Scripts\python.exe -m pytest -ra`
with both test DSNs unset. The suite uses synthetic fixtures and runs without
Yahoo or Supabase credentials.

## Technical guide

- [Architecture and daily OHLCV contract](docs/market-data-foundation.md)
- [Yahoo adapter and date-label evidence](docs/market-data-yahoo.md)
- [PostgreSQL migrations, import, queries and audit](docs/market-data-postgres.md)
- [FastAPI setup and endpoints](docs/market-data-api.md)
- [Historical news source evaluation](docs/news-provider-research.md)
- [News schema and local verification](docs/news-postgres.md)

The public checkout contains code and synthetic fixtures. Historical
archives are operator-managed. The [import guide](docs/market-data-postgres.md#historical-import)
explains how to validate a local archive set without writing to a database.

## Roadmap

The [news ingestion design](docs/news-ingestion-design.md) describes the
next local pilot. RAG, research agents and a web/PWA interface remain planned.
