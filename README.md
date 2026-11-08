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
- A read-only Supabase DEV audit verified **11,352 bars across six assets**
  and 12 ingestion runs, with zero differences against the reviewed archives.
  See the [audit results](docs/market-data-postgres.md#phase-26-dev-audit).

## Run the tests locally

Use Python 3.12 from the repository root. Create a virtual environment and
activate it for your shell before installing dependencies.

```text
python -m venv .venv
python -m pip install -r backend/requirements-dev.txt
python -m pytest -ra
```

The synthetic tests run without Yahoo or Supabase credentials. PostgreSQL
integration tests skip unless you set `MARKET_DATA_TEST_DSN` to a disposable
local PostgreSQL 17 database. Follow the [local integration setup](docs/market-data-postgres.md#local-tests)
to run the full suite.

## Technical guide

- [Architecture and daily OHLCV contract](docs/market-data-foundation.md)
- [Yahoo adapter and date-label evidence](docs/market-data-yahoo.md)
- [PostgreSQL migrations, import, queries and audit](docs/market-data-postgres.md)

The public checkout contains code and synthetic fixtures. Historical
archives are operator-managed. The [import guide](docs/market-data-postgres.md#historical-import)
explains how to validate a local archive set without writing to a database.

## Roadmap

The next stage is a FastAPI read service over the existing repository.
News retrieval and RAG, research agents, and a web/PWA interface are
planned after the data service.
