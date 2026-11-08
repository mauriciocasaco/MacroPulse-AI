# News ingestion design for Phase 3.4

Start with a small, local GDELT metadata pilot using the windows and archive
records in the [provider review](news-provider-research.md). Keep the Python
adapter behind a common record contract so an approved text source can be
added independently. Use synthetic fixtures for automated tests.

| Component | Responsibility |
| --- | --- |
| Provider adapter | Read one archive batch or one bounded API page. Return candidate records and the next cursor, distinguishing publisher, provider, external ID and archive reference. |
| Normalizer and rights policy | Normalize time and URL fields. Keep unknown publication time unset and date-only precision explicit. Record provider observation and local retrieval separately. Admit only approved fields and text levels for the source and use. |
| Asset linker | Map versioned aliases, themes and concepts to the asset catalog. Record method, evidence and link time. Route ambiguous matches for review. Keep VT and VWO macro associations distinct from explicit mentions of either fund. |
| Writer | Upsert source, article, provider record and asset associations in one transaction. Preserve original URL and provider ID. Use the canonical URL as the article identity and provider-specific keys for provenance. |
| Runner | Process bounded windows or batches and record cursors, counts, failures and adapter version. Keep scheduling and bulk backfill separate from adapter logic. |

Seed the linker with WTI/West Texas Intermediate, COMEX gold/gold futures,
S&P 500, and Bitcoin/BTC. Require fund names or provider entity IDs for
direct VT/VWO matches. Treat global equities, emerging markets and
monetary policy as candidate contextual topics. Bare two-letter fund
symbols are too ambiguous for unattended matching.

Conservatively normalize URLs by lowercasing the host, dropping fragments
and removing known tracking parameters. Preserve meaningful query parameters
and avoid resolving redirects automatically. Leave syndicated copies with
different canonical URLs separate until there is evidence they are the
same article. If a provider key already points to a different article,
quarantine the conflict instead of reassigning provenance. Replaying a batch
must preserve the original retrieval time and add only missing records or
links. Sanitize references to exclude credentials and redact logs to counts,
reason codes and safe IDs.

Write a checkpoint only after the corresponding transaction commits. A crash
between commit and checkpoint may replay the batch safely. Use one writer
per provider/partition for the pilot. Bound concurrency and requests per
run, honor `Retry-After`, use backoff with jitter for transient failures,
and stop on expired credentials or exhausted quota. Record malformed rows
without retaining raw restricted payloads. Surface unresolved windows in
the run summary.

Begin with the six tested windows and review precision, timestamp
completeness and duplication before expanding. For a 2019 onward backfill,
plan UTC partitions, report gaps and cap total files or query bytes. Treat
backfill completeness separately from live ingestion lag. Store no body
for the GDELT pilot and keep approved excerpts out of public responses
unless their display is separately authorized.

For the first local pilot, verify that a replay preserves article and record
counts, resumption after a simulated crash finishes the planned windows,
undated records stay out of publication-time results, and unknown rights
block text retention and processing.

For strict historical replay, filter by what MarketPulse had ingested and
linked by the requested cutoff. Historical discovery with trusted
publication time is a separate research mode. Keep undated records out of
publication-time joins and label any observation-time approximation. Future
embeddings can reference the article or provider record with a text version,
model version and rights decision. Add those only after the text pilot is
approved.
