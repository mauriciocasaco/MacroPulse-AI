# M1 — Contratos de series oficiales del BCE

Verificación del 9 de noviembre de 2026 (America/Montevideo). Las cinco claves
propuestas coinciden con el campo `KEY` de la API SDMX del BCE. La fecha de
verificación corresponde a la captura de la respuesta, independiente del
período de referencia y de la publicación histórica del dato.

| Indicador | Clave exacta y ficha oficial | Frecuencia y período | Unidad canónica |
| --- | --- | --- | --- |
| EUR/USD | [EXR.D.USD.EUR.SP00.A](https://data.ecb.europa.eu/data/datasets/EXR/EXR.D.USD.EUR.SP00.A) | `D`, día de la referencia | USD por EUR, multiplicador 0 |
| PIB real | [MNA.Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.G1](https://data.ecb.europa.eu/data/datasets/MNA/MNA.Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.G1) | `Q`, trimestre calendario | %, variación intertrimestral no anualizada, multiplicador canónico 0 |
| Inflación general | [HICP.M.U2.N.000000.4D0.ANR](https://data.ecb.europa.eu/data/datasets/HICP/HICP.M.U2.N.000000.4D0.ANR) | `M`, mes calendario | %, variación interanual, multiplicador 0 |
| Saldo M3 | [BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E](https://data.ecb.europa.eu/data/datasets/BSI/BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E) | `M`, saldo al cierre del mes | EUR, multiplicador 6 en origen (millones) |
| Facilidad de depósito | [FM.B.U2.EUR.4F.KR.DFR.LEV](https://data.ecb.europa.eu/data/datasets/FM/FM.B.U2.EUR.4F.KR.DFR.LEV) | `B` en origen, eventos por fecha de entrada en vigor | % anual, multiplicador 0 |

### EUR/USD

El BCE produce y distribuye la referencia spot de concertación entre bancos
centrales europeos. La unidad expresa cuántos dólares estadounidenses se
cotizan por un euro, divisa base. Es un par monetario bilateral, sin agregado
geográfico ni ajuste estacional aplicable. `D` y `SP00.A` identifican la
referencia diaria publicada, sin inferir una media intradía del mercado.
El CSV trae `UNIT=USD`, `UNIT_MULT=0`, `DECIMALS=4` y también
`UNIT_INDEX_BASE=99Q1=100`. El portal usa este último campo en el rótulo
de unidad. El valor de la referencia es el tipo de cambio directo, según la
unidad del CSV, las divisas de la clave y la tabla oficial del BCE de divisas
por euro. Se conserva el campo de índice como metadato de origen, sin usarlo
para reescalar. [Tipos de cambio de referencia del BCE](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html).
El archivo histórico oficial confirma la misma cotización de 1.1394 USD
por EUR para el 30 de junio de 2026.
[Archivo de referencias diarias del BCE](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml).

El título del CSV y la metodología del portal mencionan las 14:15 CET, mientras
la página de tipos de referencia describe la concertación alrededor de las
14:10 CET. Ambas distinguen la actualización habitual alrededor de las
16:00 CET en días laborables salvo cierres de TARGET. Las horas habituales
no son timestamps observados de cada publicación. Los cierres no generan
puntos. La referencia es informativa y el BCE desaconseja su uso en
transacciones. [Metodología EXR](https://data.ecb.europa.eu/methodology/exchange-rates).

### PIB real de la eurozona

El indicador mide el cambio del PIB a precios de mercado de la economía total
en volumen encadenado y rebasado (`B1GQ`, `LR`, año de referencia de precios
2020 en la muestra), ajustado por calendario y estacionalidad (`Y`).
`I10` fija los 21 miembros de la zona euro a 1 de enero de 2026 para toda la
serie, también en trimestres anteriores. `G1` es crecimiento respecto del
trimestre inmediatamente anterior, sin anualizar. Las cuentas nacionales
subyacentes proceden de Eurostat y las autoridades estadísticas nacionales
(ESA 2010). El BCE calcula y distribuye la serie transformada exacta.
[Dataset MNA del BCE](https://data.ecb.europa.eu/data/datasets/MNA) ·
[Metadatos de cuentas nacionales trimestrales de Eurostat](https://ec.europa.eu/eurostat/cache/metadata/en/namq_10_gdp_esms.htm).

El portal muestra «Tens of Euro» y el CSV conserva `UNIT_MEASURE=EUR`,
`UNIT_MULT=1`, `DECIMALS=1` junto con `TRANSFORMATION=G1`,
`COMPILING_ORG=4F0` y `DATA_COMP=PCT(MNA.Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N)`.
Los niveles `LR.N` de 2026-Q1 y Q2 son `3337959.03056988` y
`3359488.73529674` (millones de EUR, `COMPILING_ORG=4D0`). El cociente
`100 × (Q2/Q1 − 1)` da aproximadamente `0.6449960748375 %` y coincide
con el `OBS_VALUE=0.6449960748375227` salvo redondeo numérico.
Coincide con el 0,6 % de Eurostat redondeado a un decimal. El contrato preserva
los metadatos brutos y el Decimal del BCE, con escala porcentual canónica 0.
Aplicar el multiplicador bruto a `OBS_VALUE` daría un resultado erróneo.
[Publicación de Eurostat del 7 de septiembre de 2026](https://ec.europa.eu/eurostat/en/web/products-euro-indicators/w/2-07092026-ap).

### Inflación general interanual

Eurostat calcula el agregado a partir de los datos nacionales armonizados.
El BCE lo redistribuye (proveedor y compilador `4D0`). `000000` identifica
el IAPC general y `ANR` la variación porcentual de un mes respecto al mismo
mes del año anterior. `U2` es la eurozona con composición cambiante y `N`
significa sin ajuste estacional ni de días laborables. La unidad de origen
`PCCH` viene con multiplicador 0 y un decimal. El dato mensual de junio de
2026 fue 2,8 % tanto en el flash como en la publicación completa. Cada
publicación conserva su identidad y fecha propias.
[Metodología Eurostat](https://ec.europa.eu/eurostat/cache/metadata/en/prc_hicp_esms.htm) ·
[Flash de junio](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-01072026-ap) ·
[Publicación completa de junio](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-17072026-ap).

### Saldo de M3

El BCE compila y distribuye las estadísticas BSI del SEBC a partir de los
balances monetarios de la zona euro. El agregado amplio recoge pasivos
monetarios de las IFM, administraciones centrales e instituciones de giro
postal frente a residentes no IFM de la zona euro excluida la administración
central. Tanto el área informante como la contraparte usan `U2` de
composición cambiante. `Z01` cubre todas las monedas de transacción y
`E` expresa el saldo en EUR. `Y` aplica ajuste estacional y por días
laborables. `1` selecciona importes vivos al fin del período. El CSV
entrega `UNIT=EUR`, `UNIT_MULT=6` y `DECIMALS=2`. Un valor de
`17612461` significa 17.612.461 millones de EUR (17.612.461.000.000 EUR
si se convierte a euros). Los dos decimales son una convención de presentación.
El saldo y la tasa de crecimiento oficial son medidas distintas. El BCE
documenta revisiones y ajustes que pueden cambiar los saldos históricos.
[Metodología BSI](https://data.ecb.europa.eu/methodology/monetary-aggregates) ·
[Información estadística y política de revisión](https://data.ecb.europa.eu/data/publications/BSI01_01/data-information).

### Facilidad de depósito

El Consejo de Gobierno del BCE fija la tasa para los depósitos a un día en
el Eurosistema y el BCE publica la serie oficial. `U2` identifica la zona
euro de composición cambiante. Es un nivel de política sin ajuste estacional
aplicable. La ficha presenta `B` (semana laborable) y el CSV trae
`TIME_FORMAT=P1D`, `UNIT=PCPA`, multiplicador 0 y siete decimales de
presentación. La clave `DFR.LEV` en esta frecuencia entrega eventos de
cambio. La observación del 17 de junio de 2026 es 2,25 % anual con efecto
desde esa fecha. La decisión del BCE se anunció el 11 de junio. Consultas
sin cambios devuelven cero eventos económicos para ese intervalo una vez
verificada la cobertura. No se generan observaciones diarias.
[Decisión del BCE del 11 de junio](https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260611~4d41bd5e83.en.html) ·
[Tabla oficial de fechas efectivas](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html).

### Estados y derechos

Las cinco filas de muestra tienen `OBS_STATUS=A` (valor normal), que no
certifica carácter definitivo ni ausencia de revisiones. Se conserva cada
estado original, incluyendo valores provisionales (la ficha de M3, en la
versión indexada con última actualización del 1 de junio de 2026, mostraba
marzo de 2026 como provisional), avisos de revisión,
ausencias y códigos de confidencialidad. La revisión de una observación se
gestiona también por versión, aunque siga marcada `A`. Un anuncio de
política se registra como evidencia de vigencia o disponibilidad para su
propio contenido. No convierte la observación del proveedor en otra fecha.
[Contrato temporal](migration-plan.md#contrato-temporal) ·
[Derechos y atribución por clave](data-reuse-policy.md).

### Consultas HTTP reproducibles

PowerShell con `curl.exe`, sin autenticación. Se acota por período y número
de filas. `-i` muestra el estado y las cabeceras HTTP. Las consultas consultan
la versión de producción al momento de ejecución, por lo que un dato o una
marca pueden cambiar por una revisión. Un error HTTP, HTML o un cuerpo sin
la clave esperada no constituyen evidencia de una serie válida.

```powershell
$ecbApi = 'https://data-api.ecb.europa.eu/service/data'
$paths = @(
    'EXR/D.USD.EUR.SP00.A'
    'MNA/Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.G1'
    'HICP/M.U2.N.000000.4D0.ANR'
    'BSI/M.U2.Y.V.M30.X.1.U2.2300.Z01.E'
    'FM/B.U2.EUR.4F.KR.DFR.LEV'
)
foreach ($path in $paths) {
    curl.exe -fsiS --max-time 45 "$ecbApi/${path}?startPeriod=2026-01-01&endPeriod=2026-06-30&lastNObservations=2&format=csvdata"
    if ($LASTEXITCODE -ne 0) { throw "ECB request failed for $path" }
}

curl.exe -fsiS --max-time 45 "$ecbApi/MNA/Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N?startPeriod=2026-Q1&endPeriod=2026-Q2&format=csvdata"
```

| CSV oficial (HTTP 200) | `TIME_PERIOD` | `OBS_VALUE` | Unidad y multiplicador brutos | `OBS_STATUS` |
| --- | --- | ---: | --- | --- |
| EXR | `2026-06-30` | 1.1394 | `USD, 0` | `A` |
| MNA G1 | `2026-Q2` | 0.6449960748375227 | `UNIT_MEASURE=EUR, 1` | `A` |
| HICP | `2026-06` | 2.8 | `PCCH, 0` | `A` |
| BSI | `2026-06` | 17612461 | `EUR, 6` | `A` |
| FM | `2026-06-17` | 2.25 | `PCPA, 0` | `A` |

Verificación de respuesta y semántica por clave a partir de la
[API de datos del BCE](https://data.ecb.europa.eu/help/api/data).
