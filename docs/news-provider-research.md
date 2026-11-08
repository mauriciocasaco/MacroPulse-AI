# Historical news sources

Reviewed on 2026-11-08. The target is news discovery from 2019 onward for
gold, WTI, the S&P 500, VT, VWO and Bitcoin. API access, local retention,
LLM processing and public display are separate permissions.

## Recommendation

Start with GDELT GKG for historical discovery and attribution. The archive
returned relevant records across six small event windows, including January
2019. It can support a metadata and source-link prototype. It does not supply a
licensed full-text corpus, and publication timestamps and titles are incomplete.
For a text-based RAG pilot, use individually approved sources or evaluate
NewsAPI.ai after confirming contractual permissions and sampling coverage.

| Provider | Historical coverage and data | Cost and Python access | Retention and public use |
| --- | --- | --- | --- |
| GDELT GKG / DOC | GKG 2.x archive from 2015 with publisher domain, URL, observation timestamp and extracted themes/entities. Precise publication time is optional. DOC article lists offer titles for recent discovery. Its documented rolling window, even with the published one-year extension, does not reach 2019 or 2020. Historical DOC runtime coverage was inconclusive in the bounded check below. GKG is a metadata feed. | Public archive files require no key and have no published request quota in the reviewed documentation. Use `urllib`, `zipfile` and TSV parsing for bounded batches. For wider searches, consider `google-cloud-bigquery` with partition filters and a byte cap. On-demand query processing includes 1 TiB/month free, with pricing starting at US$6.25/TiB thereafter. | GDELT permits reuse of its datasets with project attribution and link. Treat publisher text, quotations and media as third-party material. [Archive](https://www.gdeltproject.org/data.html) · [Metadata](https://blog.gdeltproject.org/new-gkg-2-0-article-metadata-fields/) · [DOC window](https://blog.gdeltproject.org/doc-geo-2-0-api-updates-full-year-searching-and-more/) · [Terms](https://www.gdeltproject.org/about.html) · [BigQuery pricing](https://cloud.google.com/bigquery/pricing) |
| NewsAPI.ai / Event Registry | Advertises archive from 2014. Provides article URL, ID, title, publisher, time fields, concepts, categories and body. The official SDK example shows a 2019 record, and actual financial coverage across all six assets remains untested. | API key and `eventregistry` SDK or HTTPS JSON. Free evaluation includes a one-time 2,000-token allocation, 30 days of coverage and up to five concurrent requests. The archive requires a paid plan. Its 5K plan lists US$90/month and up to 1,000 historical searches at five tokens per searched year, up to 100 articles per search. | Paid terms permit internal retention and AI processing, including service providers that do not retain data for training. Public headline/excerpt display and use of publisher text remain subject to third-party rights. Raw feed redistribution requires authorization. [Free plan](https://newsapi.ai/free-news-api) · [Plans](https://newsapi.ai/plans) · [SDK](https://newsapi.ai/intro-python) · [Terms, sections 5–6](https://newsapi.ai/terms) |
| Alpha Vantage | `NEWS_SENTIMENT` accepts UTC ranges, tickers and topics, including BTC, macro and energy. Combining tickers or topics applies intersection semantics. Earliest usable date and mapping of futures, index and ETF symbols are unverified. | HTTPS JSON with key, up to 1,000 results per query. The documentation labels the function Premium. The general 25 requests/day free quota does not establish entitlement to this endpoint. Premium price for this use was not verifiable in the reviewed page. | Base terms are personal and noncommercial unless agreed otherwise. Confirm text retention, LLM and display rights separately. [Endpoint](https://www.alphavantage.co/documentation/#news-sentiment) · [Quota](https://www.alphavantage.co/support/) · [Premium](https://www.alphavantage.co/premium/) · [Terms](https://www.alphavantage.co/terms_of_service/) |
| Finnhub | Market News offers category and incremental ID. Company News filters by symbol and dates for North American companies. Its schema exposes headline, summary, source, URL and timestamp. No verified 2019 coverage for this universe, and a company endpoint is a poor fit for index and commodity instruments. | Key and official `finnhub-python` SDK. The commercial offering lists negotiated pricing and unlimited calls. The free news entitlement and quota were not verifiable in the first-party pricing page. | Verify the exact news dataset and license before retaining or sharing its output. The commercial offering advertises redistribution rights subject to agreement. [Endpoint schema](https://github.com/Finnhub-Stock-API/finnhub-go/blob/master/api/openapi.yaml) · [Python](https://github.com/Finnhub-Stock-API/finnhub-python) · [Pricing](https://finnhub.io/pricing) · [Commercial offering](https://api.finnhub.io/pricing-startups-and-enterprise) |
| NewsAPI.org | Free search covers one month, and published paid plans cover five years. As of this review, five years cannot reach 2019 or April 2020. Provides publisher, title, URL, publication timestamp and limited excerpts. | HTTPS JSON with key. Developer is free, 100 requests/day and dev/test only. Business starts at US$449/month billed monthly. | Full text is unavailable through the API. Review provider and publisher terms before reuse. [Pricing](https://newsapi.org/pricing) · [Everything endpoint](https://newsapi.org/docs/endpoints/everything) · [Terms](https://newsapi.org/terms) |

## Bounded public archive check

Read six published [GDELT Project GKG ZIP archives](https://www.gdeltproject.org/data.html)
into memory, each capped at 16 MB compressed. The initial 5 MB cap was too small.
The check selected the first URL-path match for each case. It did not fetch
publisher pages, persist article content or evaluate recall. Publisher domains
and associations below are candidates derived from the archive, not validation
of the corresponding article's text, the market move or a causal relationship.
VT and VWO share the macro sample. Direct coverage of either fund is unverified.

| Probe | GKG record ID and source domain | Publication UTC reported by GDELT | Retrieved UTC (2026-11-08) |
| --- | --- | --- | --- |
| 2019 equity market baseline | `20190102180000-299` · yahoo.com | 2019-01-02 16:21 | 23:37:37 |
| `CL=F` · negative WTI | `20200420210000-39` · courant.com | Unknown | 23:37:38 |
| `GC=F` · gold record | `20200805180000-141` · reuters.com | Unknown | 23:37:40 |
| `^GSPC` · March 2020 equity market | `20200323200000-1051` · nytimes.com | 2020-03-23 04:02 | 23:37:41 |
| `BTC-USD` · spot ETP approval | `20240110230000-388` · channelnewsasia.com | Unknown | 23:37:42 |
| `VT`, `VWO` · Fed tightening | `20220615193000-139` · lancasteronline.com | 2022-06-15 19:04 | 23:37:43 |

To reproduce, take the timestamp before the hyphen in the record ID, fetch
`https://data.gdeltproject.org/gdeltv2/<timestamp>.gkg.csv.zip` and filter the
tab-separated record by its exact first field. Verify the publisher in field
four, the document URL in field five and the optional
`PAGE_PRECISEPUBTIMESTAMP` tag in the extra XML. The second field is the GKG
observation timestamp. Do not copy it into the publication field. These
records are credited to the [GDELT Project](https://www.gdeltproject.org/).
See the [GKG 2.1 codebook](https://data.gdeltproject.org/documentation/GDELT-Global_Knowledge_Graph_Codebook-V2.1.pdf).

Two Alpha Vantage demo requests, the documented BTC-related example and a
five-result April 2020 energy query, each returned HTTP 200 with an
`Information` response and no `feed`. This leaves archive coverage
unverified. [Alpha Vantage examples](https://www.alphavantage.co/documentation/#news-sentiment)

A DOC ArticleList query for crude oil, April 20–22, 2020, requested at most
five results. The initial HTTP 200 response was not captured in a verifiable
form, and repeats returned HTTP 429 with a one-request-per-five-seconds
message. The archive evidence above is independent of this DOC probe.

For event-date checks and a small corpus of individually reviewed releases,
consider the EIA's April 27 retrospective on April 20 WTI, the Fed's March 23
release with a stated 08:00 EDT publication time, and the SEC's January 10
spot Bitcoin ETP statement. Treat the EIA's retrospective as available only
from its own publication date. [EIA](https://www.eia.gov/todayinenergy/detail.php?id=43495)
· [Federal Reserve](https://www.federalreserve.gov/newsevents/pressreleases/monetary20200323b.htm)
· [SEC](https://www.sec.gov/newsroom/speeches-statements/gensler-statement-spot-bitcoin-011023)

Before a text pilot, confirm which fields may be retained and displayed and
whether text can be sent to a model provider or embedded. For NewsAPI.ai,
creating a key and approving any paid plan are operator decisions. No key or
paid plan is needed for the GDELT archive pilot.
