# PostgreSQL news schema

Apply [`202611080002_news.sql`](../supabase/migrations/202611080002_news.sql)
after the two market-data migrations. It creates the `news` schema and references
`market_data.assets` without changing the existing market tables. The market-data
Python migration runner continues to manage the two Phase 2 migrations. The news
SQL is a separate Supabase migration prepared and exercised on disposable PostgreSQL.

| Table | Purpose |
| --- | --- |
| `news.sources` | Retrieval provider or direct source and an optional terms URL |
| `news.articles` | One canonical URL and publisher, optional headline and publication time or date |
| `news.article_records` | Provider-specific source URL, ID, evidence, timestamps, and any permitted content |
| `news.article_assets` | Many-to-many relation to market assets with association evidence and `linked_at` |

The canonical URL is unique. A source record is unique by `(source_id, external_id)`
when it has an external ID, or `(source_id, source_url)` among records with no ID.
Distinct external IDs preserve multiple observations of the same article from one
provider. Records from separate providers can also point to one canonical article.

`published_at` and `reported_published_at` are exact instants. Use `published_on`
and `reported_published_on` for date-only claims and leave publication fields empty
when evidence is missing. `provider_observed_at` records the provider's observation
time. For example, a GDELT archive timestamp can serve as observation evidence.
`retrieved_at` records when MarketPulse fetched the record. Preserve the source
record ID and a credential-free archive or response reference in `provenance_ref`.

For a future ingester, normalize a stable canonical URL and preserve the original
source URL separately. Store a provider record, its article and any asset links in
one transaction. Resolve a provider ID to its existing article before merging a
changed canonical URL. Reject ambiguous identity collisions and record sanitized
diagnostics. When source claims differ, retain the individual claims and promote
a canonical publication value only with evidence. Ingest and association code must
exclude API keys, signed URL tokens and private data from all stored references.

Record permissions per provider observation using `storage_permission`,
`public_display_allowed`, `embedding_allowed` and `rights_basis`. Defaults allow
the metadata-only storage shape, with display and embedding disabled. Confirm the
specific metadata fields permitted by the source before populating them. Store an
excerpt or full body only when the applicable rights grant permits it and provide
the relevant licence or agreement reference. Text storage, public display and
embedding are separate decisions. Ingestion must also enforce retention and
deletion requirements from the referenced terms.

Query exact publication instants in a half-open interval using bound UTC values.
Unknown and date-only publications stay out of exact-time results. The same article
can match several assets but appears once for each requested asset.

```sql
SELECT a.id, a.title, a.canonical_url, a.publisher, a.published_at
FROM news.article_assets AS link
JOIN news.articles AS a ON a.id = link.article_id
WHERE link.asset_id = $1 AND a.published_at >= $2 AND a.published_at < $3
ORDER BY a.published_at, a.id;
```

For historical research, apply retrieval and link cutoffs before exposing any
records at an as-of instant. Canonical metadata is mutable, so reproducible
as-of research also needs versioned canonical evidence or direct filtering of
the provider claims available at the cutoff.

## Local verification

With `MARKET_DATA_TEST_DSN` pointing to the [disposable PostgreSQL 17 test
instance](market-data-postgres.md#local-tests), run
`python -m pytest -ra tests/test_news_postgres.py`. The fixture creates and drops a
temporary database and applies the migrations with their checked-in schema names.
It verifies the four new tables, preservation of the market migration ledger,
deduplication, relationships, timestamps, rights constraints and period queries.
