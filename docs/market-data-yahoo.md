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

## Daily date-label evidence

BTC-USD was the first enabled date-label convention. In a bounded
public cross-check for `[2023-02-15, 2023-02-18)`, Yahoo chart metadata reported
UTC and the three raw chart labels were `00:00:00Z` on February 15, 16, and 17.
Their closes matched to the published precision on Yahoo's own BTC-USD daily
history for those dates. The chart also returned the exclusive February 18
boundary row, which the normalizer rejects. This supports interpreting those
BTC-USD chart timestamps as UTC date labels. It does not verify a daily cutoff,
market-close time, session status, or conventions for any other symbol.

The mapping identifier is `verified:yahoo_bitcoin_utc_date:v1`. The normalizer
accepts it only for BTC-USD when the response timezone is UTC and each Unix
label is at UTC midnight, including weekends. The public
cross-check used Yahoo's own [BTC-USD historical prices](https://es.finance.yahoo.com/quote/BTC-USD/history/?filter=history&frequency=1d&includeAdjustedClose=true&interval=1d&period1=1609372800&period2=1703980800).


## Etapa 1B · Fecha de sesión de ^GSPC, VT y VWO

Se verificó la fecha de sesión de estos tres símbolos en el intervalo
`[2019-11-08, 2026-11-08)`. Yahoo declaró `America/New_York`. Cada uno de los
tres lotes contiene 1.758 etiquetas a las 09:30 hora local y el conjunto de
fechas coincide exactamente con las sesiones de referencia de NYSE Group. Se
incluyeron cierres abreviados y se excluyeron fines de semana, festivos con
cierre total y el cierre extraordinario del 9 de enero de 2025.
[Calendario 2019–2021](https://ir.theice.com/press/news-details/2018/NYSE-Group-Announces-2019-2020-and-2021-Holiday-and-Early-Closings-Calendar/default.aspx) ·
[Calendario 2022–2024](https://ir.theice.com/press/news-details/2021/NYSE-Group-Announces-2022-2023-and-2024-Holiday-and-Early-Closings-Calendar/default.aspx) ·
[Calendario 2024–2026](https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx) ·
[Cierre extraordinario de 2025](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx)

| Símbolo | Referencia de sesión | Close del 2026-03-06 y del 2026-03-09 según Yahoo |
| --- | --- | --- |
| ^GSPC | S&P DJI calcula los índices estadounidenses cuando abre su mercado de renta variable. [Metodología](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf) | 6740,02 y 6795,99 puntos. [Histórico de Yahoo](https://finance.yahoo.com/quote/%5EGSPC/history/) |
| VT | Cotiza en NYSE Arca, cuya sesión regular abre a las 09:30 ET. [Prospecto](https://www.sec.gov/Archives/edgar/data/857489/000168386324000954/f37613d1.htm) · [NYSE Arca](https://www.nyse.com/trade/hours-calendars) | 141,93 y 143,24 USD. [Histórico de Yahoo](https://ca.finance.yahoo.com/quote/VT/history/) |
| VWO | Cotiza en NYSE Arca y usa su sesión estadounidense, aunque los subyacentes coticen en otros mercados. [Prospecto](https://www.sec.gov/Archives/edgar/data/857489/000119312526077568/f44207d1.htm) · [NYSE Arca](https://www.nyse.com/trade/hours-calendars) | 54,47 y 55,13 USD. [Histórico de Yahoo](https://finance.yahoo.com/quote/VWO/history/?p=VWO) |

Los Close archivados de esos dos días coinciden con los de Yahoo a la precisión
publicada. Los epoch `1772807400` (viernes 14:30Z, EST) y `1773063000` (lunes
13:30Z, EDT) muestran el cambio de desplazamiento UTC sin cambiar la hora
local de la etiqueta. Los tres símbolos incluyen la sesión abreviada del
2025-11-28. En la transición de otoño, los epoch `1793367000` (2026-10-30,
13:30Z, EDT) y `1793629800` (2026-11-02, 14:30Z, EST) vuelven a coincidir a
las 09:30 locales. Los tres símbolos tienen como primera y última fecha
2019-11-08 y 2026-11-06. El
Close archivado del índice en la primera fecha coincide con 3093,08 en la
serie atribuida a S&P DJI en FRED. En la última fecha, Yahoo publica 7376,86
para ^GSPC y 55,29 para VWO, en ambos casos coincidentes con el archivo.
[Calendario de cierres abreviados](https://ir.theice.com/press/news-details/2024/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar/default.aspx) ·
[S&P 500 en FRED](https://fred.stlouisfed.org/data/SP500)

La convención `verified:yahoo_us_equity_open_date:v1` exige el mapeo canónico
de uno de los tres símbolos, la zona declarada por Yahoo y un Unix epoch a
las 09:30 de Nueva York en una sesión del calendario revisado. Las fechas
fuera del calendario acotado no son elegibles para esta regla. La etiqueta
identifica la fecha de la sesión. El corte de la barra, la hora en que
puede conocerse el Close y el estado al recuperar el dato requieren
evidencia propia. Las barras archivadas conservan `session_status=unknown`.

El normalizador conserva el mapeo original `unknown` en la procedencia del
reproceso y aplica la regla vigente solo si el resto del mapeo coincide
exactamente con el catálogo. Los gzip, la metadata del proveedor y
`retrieved_at` quedan intactos. El corte declarado en el lote sigue sin
verificar. El adaptador usa el mismo contrato de adquisición.

## Etapa 1B · Fecha oficial de negociación de GC=F y CL=F

GC de COMEX y CL de NYMEX operan en CME Globex. El horario se publica en
hora de Chicago, mientras Yahoo declaró `America/New_York`. En una sesión
habitual la negociación puede comenzar a las 17:00 de Chicago del día
anterior, incluso el domingo para el lunes. Por ello una fecha calendario
de un instante nocturno o de un festivo no determina por sí sola la fecha
de negociación. [Especificaciones de GC](https://www.cmegroup.com/content/dam/cmegroup/market-regulation/files/gold-futures-and-options-fact-card.pdf) ·
[Horarios de CL](https://www.cmegroup.com/education/courses/event-contracts-underlying-markets/wti-overview)

Cada archivo contiene 1.749 etiquetas a medianoche de Nueva York y 14 a las
09:30, estas últimas en fechas de cierre abreviado de NYSE. La coincidencia
sugiere un patrón de etiquetado de Yahoo. No establece que los futuros usen
las sesiones del mercado de acciones. Los dos lotes incluyen cinco fechas
adicionales con respecto a ese calendario: 2023-11-23, 2025-01-09,
2025-05-26, 2025-06-19 y 2025-07-04.

| Fecha o contraste | GC=F | CL=F | Evidencia y límite |
| --- | --- | --- | --- |
| 2026-03-06 y 2026-03-09, transición EST–EDT | 5158,70 y 5103,70 USD/oz troy | 90,90 y 94,77 USD/barril | Los Close coinciden con las fechas de las páginas de Yahoo. El epoch se mueve de 05:00Z a 04:00Z y sigue a medianoche local. CME analiza la actividad overnight del lunes 9 para el CL de abril, sin vincularla al agregado diario de Yahoo. [GC](https://finance.yahoo.com/quote/GC%3DF/history/?frequency=1d) · [CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF) · [CME](https://www.cmegroup.com/openmarkets/energy/2026/What-History-Reveals-About-Todays-Crude-Oil-Volatility.html) |
| 2025-11-28, sesión abreviada | 4254,90 | 58,55 | Close coincidente con Yahoo y etiqueta a las 09:30 en ambos. CME publicó horarios especiales de liquidación. [GC](https://finance.yahoo.com/quote/GC%3DF/history/?p=GC%3DF) · [CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF) · [CME](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/thanksgiving-holiday-settlement-times-2025.pdf) |
| 2025-01-09, duelo nacional | 2690,80 | 73,92 | CME anunció horarios regulares de energía y metales y reapertura vespertina para el día siguiente, mientras NYSE cerró por completo. [CME](https://www.cmegroup.com/media-room/press-releases/2025/12/30/cme_group_announcestradinghoursforusnationaldayofmourningtohonor.html) |
| 2025-07-04 | 3346,50 | 66,50 | Yahoo conserva una fila fechada el viernes sin liquidación oficial de CME. OCC informa que las operaciones se procesaron para el lunes 7. CME documenta un cambio posterior en la alineación de fechas Globex y Clearing para festivos de viernes. Con estas referencias la atribución de las barras de Yahoo sigue ambigua entre las fechas de calendario, negociación y procesamiento. [CME liquidaciones](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/us-independence-day-settlement-times-2025.pdf) · [OCC](https://infomemo.theocc.com/infomemos?number=56777) · [CME aviso de 2026](https://www.cmegroup.com/notices/electronic-trading/2026/01/20260119.html) |
| 2020-04-20 | — | −37,63 | El Close negativo archivado coincide con el precio de liquidación documentado por EIA para el contrato cercano de mayo. La política de signo permite ese valor. [EIA](https://www.eia.gov/TodayinEnergy/detail.php?id=43875) |

Las dos filas de GC y tres de CL sin Close siguen inválidas en el chequeo
financiero independiente. CME no publicó liquidación oficial en las fechas
correspondientes. Esta coincidencia deja abierta la definición de Close en
Yahoo y los valores siguen ausentes.
[Thanksgiving 2023](https://www.cmegroup.com/tools-information/holiday-calendar/files/thanksgiving-holiday-settlement-times-2023.pdf) ·
[Memorial Day 2025](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/memorial-day-holiday-settlement-times-2025.pdf) ·
[Juneteenth 2025](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/juneteenth-day-settlement-times-2025.pdf)

En la Etapa 1B, los dos mapeos se mantuvieron en `unknown` bajo el criterio
de verificar la fecha oficial de negociación. Sigue faltando una fuente
que relacione cada barra continua de Yahoo con la sesión CME, incluido el
tratamiento de festivos y sesiones nocturnas. La metodología de roll continúa
pendiente por separado.

## Reproceso local y futura persistencia

El informe `data/backfills/report-1b.md` conserva el resultado de la Etapa 1B.
Separa los rechazos del mapeo archivado, los rechazos de esa etapa y las
anomalías financieras independientes. Compara las fechas normalizadas
de ^GSPC, VT y VWO con el calendario documentado arriba.

Las observaciones de BTC-USD, ^GSPC, VT y VWO quedan normalizadas localmente.
La Fase 2.2 incorpora al repositorio las seis tuplas Yahoo revisadas. La
persistencia requiere aplicar la migración incremental y que la convención
de cada observación coincida con el mapeo persistido.

## Etapa 1C · Fecha diaria que Yahoo publica para GC=F y CL=F

Cada lote tiene 1.763 etiquetas en días laborables de Nueva York entre
2019-11-08 y 2026-11-06. En cada símbolo hay 1.749 a medianoche (1.157 con
UTC−04:00 y 592 con UTC−05:00) y 14 a las 09:30 (tres con UTC−04:00 y once
con UTC−05:00). No hay duplicados de epoch ni de fecha local, fechas fuera
del rango pedido ni etiquetas de fin de semana.

| Fecha local | Hora de Nueva York | Close GC=F / CL=F | Contraste |
| --- | --- | --- | --- |
| 2019-11-08 | 00:00 EST | 1461,30 / 57,24 | Inicio de ambos lotes. Coincide el Close de GC con la tabla histórica de Yahoo. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?int=&period1=1506297600&period2=1664064000) |
| 2019-11-29 | 09:30 EST | 1465,60 / 55,17 | Yahoo fecha GC en ese viernes de sesión abreviada. La serie EIA del primer contrato WTI corrobora el precio de CL. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?filter=history&frequency=1d&includeAdjustedClose=true&interval=1d&period1=1492732800&period2=1650499200) · [EIA CL](https://www.eia.gov/dnav/pet/hist/RCLC1D.htm) |
| 2019-12-24 | 09:30 EST | 1499,10 / 61,11 | Yahoo publica GC con esa fecha y Close. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?int=&period1=1506297600&period2=1664064000) |
| 2025-07-04 | 00:00 EDT | 3346,50 / 66,50 | Una noticia publicada ese día recoge ambos precios. El aviso de CME indica que ese festivo no hubo liquidación oficial. [Baystreet en Yahoo](https://ca.finance.yahoo.com/news/tsx-ends-friday-virtually-unchanged-202600056.html) · [CME](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/us-independence-day-settlement-times-2025.pdf) |
| 2025-11-28 | 09:30 EST | 4254,90 / 58,55 | Ambos Close y fechas coinciden con los históricos de Yahoo en una sesión de horario especial. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?p=GC%3DF) · [Yahoo CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF) · [CME](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/thanksgiving-holiday-settlement-times-2025.pdf) |
| 2026-03-06 y 2026-03-09 | 00:00 EST → 00:00 EDT | 5158,70 / 90,90 y 5103,70 / 94,77 | Los Close de ambos coinciden en la transición de primavera. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?frequency=1d) · [Yahoo CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF) |
| 2026-10-30 y 2026-11-02 | 00:00 EDT → 00:00 EST | 3898,90 / 104,12 y 3969,60 / 96,46 | Los Close de ambos coinciden en la transición de otoño. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?p=GC%3DF) · [Yahoo CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF) |
| 2026-11-06 | 00:00 EST | 4074,90 / 90,83 | Última fecha archivada. Ambos Close coinciden con las tablas publicadas. [Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?p=GC%3DF) · [Yahoo CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF) |

La convención `verified:yahoo_futures_published_date:v1` aplica solo a los
mapeos canónicos de GC=F y CL=F de Yahoo. Exige un epoch entero, zona declarada
`America/New_York` y hora local exacta 00:00 o 09:30. Usa la fecha local sin
desplazarla al día anterior de Chicago ni al próximo día de negociación.
Acepta ambos horarios en cualquier día laborable que cumpla el rango
semiabierto, incluidos festivos. El filtro de fines de semana describe el
alcance de las etiquetas observadas y no representa el calendario de CME.
Una etiqueta de forma desconocida se rechaza individualmente. Se verificaron
muestras fechadas y el patrón de ambos archivos completos. Las fuentes
externas citadas no abarcan todas las filas.

`bar_date` es la fecha diaria atribuida por Yahoo para estos futuros.
`exchange_trade_date`, `settlement_time` y `availability_time` siguen
en NULL. `rolling_method` queda en `unknown`. El instante archivado de
recuperación se conserva en UTC y tiene significado independiente.

Las dos filas de GC=F sin Close (2025-05-26 y 2025-06-19) y las tres de CL=F
(2023-11-23, 2025-05-26 y 2025-06-19) se rechazan con `missing_close`.
Los valores ausentes se conservan en los gzip originales. El cierre negativo
de CL=F del 2020-04-20 permanece como Decimal.

`python -m backend.market_data.backfill --offline` genera
`data/backfills/report-1c.md` y un archivo JSON comprimido por activo
en `data/backfills/normalized/<inicio>_<fin_exclusivo>/`. Cada lote
contiene aceptadas, rechazadas, versión y procedencia, y enlaza al gzip
original por ruta y SHA-256. Los importes Decimal se serializan como texto
y NULL conserva su significado. No se modifica el gzip original. La
verificación en PostgreSQL y la carga desde estos archivos quedan para
fases posteriores.
