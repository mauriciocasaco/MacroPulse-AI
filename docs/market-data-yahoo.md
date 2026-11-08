# Yahoo Finance adapter and date-label evidence

The adapter uses pinned yfinance 1.8.0 transport internals to capture the
Yahoo chart JSON before DataFrame processing. It first requests metadata
to determine the declared timezone, then requests one symbol at daily
frequency with exchange-local midnight bounds and an exclusive end. The
isolated session closes after retrieval. The raw batch retains Unix
labels, the provider timezone, response values and the UTC retrieval time.

Null and zero remain distinct. The adapter retains provider-reported
adjusted close separately and applies no price adjustment or repair.
Unexpected symbols, invalid timezone metadata and incompatible arrays
fail with classified errors. Rows returned outside the requested bounds
remain in the raw archive for the normalizer to evaluate. The version
guard detects incompatible yfinance releases. Upstream changes to
Yahoo's chart or authentication behavior can still affect acquisition.

## Verified label conventions

| Mapping | Normalization rule | Scope |
| --- | --- | --- |
| BTC-USD | `verified:yahoo_bitcoin_utc_date:v1` accepts UTC midnight epochs. | UTC calendar date including weekends. A public check for 2023-02-15 through 2023-02-17 matched Yahoo's daily closes. The exclusive end row was rejected. [Yahoo history](https://es.finance.yahoo.com/quote/BTC-USD/history/) |
| ^GSPC, VT, VWO | `verified:yahoo_us_equity_open_date:v1` requires 09:30 New York time on a reviewed U.S. equity session. | Each archived series has 1,758 dates matching the reference calendar in the audited window. Daylight saving transitions, abbreviated sessions and the extraordinary January 2025 closure were checked. [2019–2021 calendar](https://ir.theice.com/press/news-details/2018/NYSE-Group-Announces-2019-2020-and-2021-Holiday-and-Early-Closings-Calendar/default.aspx) · [2022–2024 calendar](https://ir.theice.com/press/news-details/2021/NYSE-Group-Announces-2022-2023-and-2024-Holiday-and-Early-Closings-Calendar/default.aspx) · [2024–2026 calendar](https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx) · [January 2025 closure](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx) |
| GC=F, CL=F | `verified:yahoo_futures_published_date:v1` accepts weekday epochs at 00:00 or 09:30 New York time. | The resulting date is Yahoo's published label. Each archive had 1,749 midnight and 14 09:30 labels. Spot checks against published dates included daylight saving transitions and shortened sessions. [GC=F history](https://finance.yahoo.com/quote/GC%3DF/history/) · [CL=F history](https://finance.yahoo.com/quote/CL%3DF/history/) |

On July 4, 2025, both futures had a Yahoo date label even though CME
published no official settlement for that holiday. This is one reason
the futures rule preserves the provider label and leaves the official
trade date and settlement time unset. The archived negative WTI close
on April 20, 2020 agrees at published precision with the near-month
price documented by EIA. [CME holiday notice](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/us-independence-day-settlement-times-2025.pdf) · [EIA WTI report](https://www.eia.gov/TodayinEnergy/detail.php?id=43875)

The [daily OHLCV contract](market-data-foundation.md#daily-ohlcv-and-provenance)
describes the distinction between date labels, session status and
availability. Offline backfill keeps the original raw gzip files and UTC
retrieval times while applying the reviewed conventions. See the
[import guide](market-data-postgres.md#historical-import).
