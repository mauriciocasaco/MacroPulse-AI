# Yahoo Finance adapter decisions

## Post-audit contract authorization

After the three ELABORADO audit rounds, the project owner explicitly
authorized the private transport in `yfinance==1.8.0` to obtain the original
Yahoo chart JSON. This exception supersedes the requirements to call
`download()`, set its processing options, and handle flat and MultiIndex
DataFrame columns. The requirements for data fidelity, provenance, dates,
OHLCV, and missing values still apply. The final audit verdict remains
REJECT (9.29/10) under the original contract. This authorization came after
that verdict and does not constitute a formal PASS.

## Acquisition and compatibility

The adapter is pinned to `yfinance==1.8.0`. That release's public history
DataFrame path fills missing `Volume` with zero and substitutes `Close` when
Yahoo omits `Adj Close`, even when `keepna=True`. The public multi-download
path also catches provider errors and logs their representations. Those
transformations do not meet the raw-data contract.

To keep yfinance responsible for transport while capturing the provider
response before pandas processing, the adapter constructs a private `YfData`
instance without registering it in yfinance's process-wide singleton. It uses
an independent `curl_cffi` session and closes it after each retrieval. It first
requests a small chart response to read the exchange timezone. It then requests
the selected range using `period1` and `period2` at exchange-local midnight,
with the end exclusive. Only the second chart response becomes the `RawBatch`.
`retrieved_at` records when that response arrived. Raw Unix timestamps are kept
as integers, and Yahoo's timezone name and offset are recorded as metadata.
The offset and timezone label do not establish a verified daily-session cutoff.
If the timezone is unavailable or invalid, acquisition fails instead of
assuming UTC. The isolated yfinance data instance does not use yfinance's
process-wide persistent cookie cache.

The chart response is parsed directly as JSON arrays. A JSON null remains
`None`, numeric zero stays zero, and `Adj Close` is included only when Yahoo
returns the `adjclose` indicator. No forward fill, interpolation, rounding,
adjustment or repair occurs. WTI values may be negative. The adapter does not
convert raw timestamps into session dates.

An omitted `adjclose` indicator leaves the field absent. A present indicator
with malformed structure or values that do not align with the timestamp
array is an incompatible response. Unrecognized quote fields are preserved
unless their names are reserved for canonical OHLCV or `Adj Close`, in which
case the response is rejected to protect field provenance.

Both chart responses must report the exact requested ticker. HTTP error status,
invalid JSON and Yahoo `chart.error` values produce provider errors without
including response bodies. Returned timestamps are checked against the exact
epoch interval `[period1, period2)`. Rows outside that interval stay in the raw
batch and receive a warning with the offending epoch values. This preserves
provider evidence while making an inclusive-end anomaly visible for
normalization review.

`auto_adjust=False`, `back_adjust=False`, and `repair=False` are satisfied by
stopping before the yfinance transformation functions run. `keepna=True` has
the equivalent effect of retaining JSON nulls. Those are history-processing
options and are not sent as chart endpoint query parameters. `interval="1d"`,
`includePrePost=False`, the exclusive period bounds, and a 20-second timeout
are sent explicitly. `actions=False` is represented by not processing action
arrays. The download-only options `threads`, `progress`, `group_by`, and
`multi_level_index` do not apply to a one-symbol raw chart response.

The adapter depends on private yfinance 1.8.0 `YfData` and chart URL internals
to avoid the lossy public DataFrame boundary and shared singleton. A version
guard prevents silent use with a different yfinance release. Any dependency
upgrade requires a new boundary check. Private constructors, session handling,
authentication and the Yahoo chart response can change independently of the
adapter. The version guard detects a different yfinance release. It cannot
detect upstream changes in Yahoo's response or authentication behavior.
A synthetic fixture exercises yfinance's DataFrame transformation next to the
raw JSON adapter output.

Empty responses with no provider reason are reported as indeterminate. Provider
exceptions are translated to short codes without retaining response payloads.
The session uses yfinance's Yahoo cookie and crumb flow without persisting the
cookie or crumb. No retries are added by the adapter. Its session sends one
Yahoo chart metadata lookup and one Yahoo historical chart request per
mapping. GC=F and CL=F rolling methodology remains unknown.

The raw chart response supplies an array per quote field, so flat and MultiIndex
DataFrame layout tests are not applicable at this boundary. One-symbol requests
keep the symbol identity attached to the mapping, and the adapter returns
provider fields without constructing pandas objects.
