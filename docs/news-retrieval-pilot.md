# Historical news retrieval pilot

## Phase 3.4 · GDELT sample

The pilot selects one record from each of the six archive windows in the
[provider review](news-provider-research.md). The selection is purposive and
supports verification of the pipeline. Coverage below describes the selected
records, so it cannot estimate provider coverage or association quality.

The six selected rows were present in their original GKG 2.1 ZIPs. Each row
had 27 tab-separated fields and a web source collection code of 1. The table
reports UTC and retains the publication timestamp as encoded by the provider.
All three reported values end in `00` seconds. The publisher's actual
timestamp precision was not independently verified.

| GKG record ID | Publisher domain | Observed UTC | Reported publication UTC |
| --- | --- | --- | --- |
| `20190102180000-299` | yahoo.com | 2019-01-02 18:00 | 2019-01-02 16:21:00 |
| `20200420210000-39` | courant.com | 2020-04-20 21:00 | Unknown |
| `20200323200000-1051` | nytimes.com | 2020-03-23 20:00 | 2020-03-23 04:02:00 |
| `20200805180000-141` | reuters.com | 2020-08-05 18:00 | Unknown |
| `20220615193000-139` | lancasteronline.com | 2022-06-15 19:30 | 2022-06-15 19:04:00 |
| `20240110230000-388` | channelnewsasia.com | 2024-01-10 23:00 | Unknown |

Retrieval took place between 2026-11-08 23:59:27 and 23:59:35 UTC. Event
times are unknown. Observed coverage spans 2019-01-02 to 2024-01-10.
Reported publication coverage spans 2019-01-02 to 2022-06-15.
GDELT supplies the URL, publisher domain, extracted themes and entities.
The pilot stores that metadata and leaves titles and article text empty.
Metadata is credited to the [GDELT Project](https://www.gdeltproject.org/).
See the [terms](https://www.gdeltproject.org/about.html) and
[GKG 2.1 codebook](https://data.gdeltproject.org/documentation/GDELT-Global_Knowledge_Graph_Codebook-V2.1.pdf).

For a local rerun, start a fresh loopback PostgreSQL 17 instance using
`pgvector/pgvector:0.8.2-pg17` and follow the
[disposable test workflow](market-data-postgres.md#local-tests) for isolation
and TLS. Apply the SQL files in `supabase/migrations` in filename order to
the disposable database. Set `MARKETPULSE_NEWS_LOCAL_DSN` for that instance
only. Download each selected archive as described in the provider review,
keeping its original filename and recording the acquisition time with a UTC
offset. Run once per archive, first with `--dry-run` and then without it.

```powershell
python -m backend.news.ingest $archivePath --record-id $recordId --retrieved-at $acquiredAt --dry-run
```

Repeat a committed batch using the same file and selection to exercise
checkpoint replay. The batch key distinguishes provider, archive,
adapter version and selected IDs. The checkpoint records the archive
SHA-256 and the original batch outcome. When aggregating CLI summaries,
count writes only for results with `checkpoint_replay=false`.

The dry-run processed six selected records and normalized all six with
zero writes. The first ingestion inserted six articles and six provider
records, with zero duplicates, rejects or identity conflicts. The second
ingestion reported six checkpoint replays. Counts stayed at six articles,
six records and six checkpoints. The database had zero price bars and
zero article bodies.

| Asset | Record associations |
| --- | --- |
| GC=F | 1 thematic |
| CL=F | 1 thematic |
| ^GSPC | 2 thematic |
| VT | 4 macro |
| VWO | 4 macro |
| BTC-USD | 1 direct Bitcoin mention |

These rules associate metadata with instruments. In particular, the
Bitcoin record concerns spot products, and the VT and VWO records provide
macro context with no evidence of a named fund mention.

## Phase 3.5 · Embedding selection

Use `intfloat/multilingual-e5-small`, revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`. The model emits 384 dimensions,
uses `query:` and `passage:` prefixes, and supports English, Spanish and
other languages. Its MIT license covers the model. Evaluate corpus rights
separately. CPU inference uses local weights and has no per-call API fee.
The general retrieval benchmarks do not establish performance on financial
news. The model card describes a 512-token limit and weaker performance in
some low-resource languages. [Pinned model card](https://huggingface.co/intfloat/multilingual-e5-small/tree/614241f622f53c4eeff9890bdc4f31cfecc418b3)

The pilot uses exact cosine search in PostgreSQL with pgvector. The sample
fits a filtered scan, so there is no approximate index to tune. Measure
candidate counts and query latency on a representative corpus before
comparing indexes. [pgvector documentation](https://github.com/pgvector/pgvector)

Migration `202611080004_news_embeddings.sql` requires pgvector in the
`extensions` schema. It installs the extension there on a fresh database.
Use a fresh disposable database if an existing installation places it in
a different schema.

Keep model weights and dependencies outside the checkout. Populate the
Hugging Face cache at the pinned revision with the model and tokenizer
configuration, pooling configuration and Safetensors weights. The pilot
used `model.safetensors` SHA-256
`1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`.
Use offline inference once cached. Keep GDELT records metadata-only and
exercise the vector path with separately authorized text or explicitly
labeled synthetic documents.

In a dedicated Python 3.12 environment, install the optional inference
runtime used for this Windows CPU pilot. Download the revision once into
the cache passed to `E5Small`, then set `HF_HUB_OFFLINE=1` for inference.

```powershell
python -m pip install 'torch==2.15.0+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install 'sentence-transformers==5.2.0' 'transformers==5.22.2'
```

The offline CPU smoke test used Python 3.12, PyTorch 2.15.0+cpu,
Transformers 5.22.2, Sentence Transformers 5.2.0 and two CPU threads.
Model initialization took 2.33 seconds. Median inference time across 15
warmed calls with short inputs was 17.2 ms on this host. Both synthetic
passages persisted as 384-dimensional vectors. Reprocessing each returned
`unchanged`. For a Spanish query about falling WTI prices, pgvector ranked
the synthetic English WTI passage first at cosine 0.834, ahead of an
unrelated synthetic passage about Bitcoin at 0.746. This is an integration
smoke test on two passages. Financial retrieval quality requires a
separate labeled corpus.

## Phase 3.6 · Retrieval checks

Structured filters scope candidates by asset, source and time window.
The window can use provider observation, reported publication or event
time. Exact timestamp windows exclude date-only records. An exclusive
historical cutoff requires both reported publication and provider
observation to precede it. System replay additionally checks ingestion
and association availability. Archive mode uses provider timestamps as
a retrospective proxy and labels that limitation.

The GDELT smoke check returned the expected metadata record for each of
six instrument queries. It returned insufficient evidence for an
unrelated query within the window, an out-of-coverage window, an
excluded provider, a strict system replay before local ingestion and
an archive cutoff on an article with unknown publication. All eleven
checks used the selected six records, so they verify behavior of the
filters and output contract on that sample.

The Python entry point is `backend.news.retrieval.search`. It accepts an
existing PostgreSQL connection, a `Search` request with timezone-aware
timestamps, and an optional embedding provider. A candidate needs either
lexical overlap or cosine similarity at least `Search.min_cosine_similarity`.
The default is 0.75. A vector below the threshold contributes no semantic
score, so the candidate can still qualify through lexical overlap. The
threshold is a pilot heuristic to calibrate with labeled data for each
model and corpus. A relevance score orders candidates within a query.
With E5 and both synthetic passages in the same WTI scope, the default
threshold returned only the relevant passage. At an explicit threshold of
0.70, both passages appeared and the relevant one ranked first.

The reproducible PostgreSQL fixture in `tests/test_news_pilot.py` has
six positive queries, one per instrument, and two no-evidence queries.
At K=1, Hit, Recall, Precision and MRR were each 1.00 on the six positive
cases. Neither negative case returned evidence. The test also checks
observation windows, synthetic labels and source provenance. The fixture
uses controlled lexical clues and is a regression check for the ranking
and filtering logic.

## Phase 3.7 · LLM design

1. Resolve instruments through the existing catalog and validate the date
   window and as-of cutoff. Read prices through the existing read-only
   repository and preserve units, adjustment conventions and timestamps.
2. Retrieve news using the same cutoff and explicit availability mode.
   Carry through record IDs, provider, original URL, timestamps, rights and
   association evidence. Treat all retrieved fields as untrusted data.
3. Pass a bounded evidence packet to a model adapter. Require citations by
   evidence ID and a separate limitations field. Validate IDs against that
   packet and keep price observations separate from causal interpretation.
   Return an evidence-limited response when the corpus cannot answer.
4. Start with ordinary Python functions and a typed response. Consider a
   workflow framework if branching or resumable multi-step execution
   becomes a concrete requirement. Build an offline evaluation before
   choosing a model or deploying.

The minimum next step for article-based evaluation is a small labeled
corpus of releases or articles with verified publication timestamps and
permission for storage, embedding and any planned display. Record rights
per document and include both relevant and irrelevant evidence for each
question.
