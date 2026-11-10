# MacroPulse AI — Registro de migración

## Línea de base

- La fase 4.4A sigue recuperable desde el tag anotado `baseline/phase-4.4a`,
  cuyo destino es `06fcad69370fe60de9121e9e19554819b9282a00`.
- M1 quedó en `c916b9f115b86420e923c61eb2f51265987ee93b` sobre
  `codex/macropulse-migration`. Auditoría independiente informada de 9,65/10.
- Las [definiciones y muestras](official-series-contracts.md),
  [condiciones de reutilización](data-reuse-policy.md) y el
  [plan temporal](migration-plan.md) de M1 siguen siendo el contrato de partida.

## M2 — Persistencia e ingesta

Ingesta local y DEV verificadas. La ronda 1 de auditoría devolvió REJECT
(8,95/10). Se corrigió el hallazgo alto de reinicios de conexión durante
la lectura del cuerpo. El reintento abarca ese fallo y, si se agota,
registra el lote fallido y permite continuar con las demás series.
La ronda 2 devolvió PASS (9,51/10), sin hallazgos altos o críticos.
Commit de etapa: `feat(macro): implement ECB and Eurostat ingestion`.
El esquema dedicado conserva las migraciones y el almacenamiento de
MarketPulse AI.

| Indicador | Identificador interno y canal | Unidad y frecuencia |
| --- | --- | --- |
| EUR/USD | `eur-usd`, BCE EXR | USD por EUR, diario, escala 0 |
| Saldo M3 | `m3-stock`, BCE BSI | Millones de EUR, saldo mensual ajustado, escala 6 |
| Facilidad de depósito | `deposit-rate`, BCE FM | % anual, fecha efectiva de cada evento, escala 0 |
| PIB real | `real-gdp-qoq`, Eurostat `namq_10_gdp` | % intertrimestral no anualizado, eurozona fija EA21, ajuste estacional y de calendario, escala 0 |
| IAPC general | `hicp-yoy`, Eurostat `prc_hicp_minr` | % interanual mensual, eurozona cambiante EA, sin ajuste, escala 0 |

Los adaptadores validan selección, dimensiones y unidad antes de promover
un lote. Se conservan el cuerpo oficial, huella, URL, cobertura, momento de
captura, reintentos y estados del proveedor. El almacenamiento guarda el
valor de origen como Decimal y su escala por separado. Una captura de
Eurostat conserva la actualización de dataset y las etiquetas oficiales
como metadatos de lote. El PIB EA21 tiene datos retrospectivos aunque la
etiqueta de área se refiere a los 21 miembros desde 2026. El IAPC mantiene
la clasificación de la ruta directa. No se mezclan canales ni se afirma
igualdad histórica con las series redistribuidas del BCE.

La conciliación se serializa por indicador y conserva las revisiones
inmutables. Una nueva descarga del mismo valor y metadatos enlaza a la
misma revisión. Un cambio de valor o estado genera otra revisión, incluso
si el valor vuelve a uno anterior. Una captura fuera de orden conserva su
cuerpo y trazabilidad, con un contador de observaciones que se excluyeron
por antigüedad. La consulta temporal normalizada usa sólo revisiones
promovidas. Una recepción excluida queda disponible como evidencia en el
registro bruto y puede quedar fuera de un corte histórico normalizado.
Un error de proveedor se registra y permite procesar los
otros indicadores. Cada persistencia de lote es transaccional. Una
respuesta vacía de la ruta de eventos DFR se registra como cero eventos.
Un error, un cierre de mercado, un valor explícitamente ausente y una
retirada del proveedor son conceptos distintos.

El modo opcional de historial del BCE conserva acciones y fechas de
vigencia explícitas de SDMX en un conjunto de evidencia separado de las
capturas corrientes. La disponibilidad local se consulta por el tiempo
de captura y las fechas de vigencia de origen no se usan como fechas de
primera publicación. En el caso de Eurostat, se conservan versiones
observadas localmente. Una respuesta actual no prueba la secuencia
histórica completa. Para visualizar la facilidad de depósito desde 2019,
el evento anterior se consulta por separado en el rango que comienza el
16 de marzo de 2016. La observación conserva su fecha efectiva de origen.

### Verificación reproducible

Ejecutar desde la raíz. Las pruebas de base de datos usan únicamente
PostgreSQL desechable en loopback mediante `MACRO_DATA_TEST_DSN`. El
driver local de ingesta exige una base preparada con nombre
`macropulse_m2_*` o `phase3_test_*`. La migración debe aplicarse primero en
esa base desechable.

```powershell
python -m pytest -q tests/test_macro_data.py tests/test_macro_data_dev.py
python -m backend.macro_data.ingest --start 2019-01-01 --end 2026-11-09
python -m backend.macro_data.ingest --indicator m3-stock --start 2026-06-01 --end 2026-06-30 --ecb-history
```

La suite focalizada pasó 13 pruebas con PostgreSQL desechable y la suite
completa pasó 386. La verificación focalizada cubrió
validación de dimensiones y escala, Decimal, fechas bisiestas y
efectivas, ausencias, fallo parcial, duplicados, conciliación concurrente,
corte temporal, historial del proveedor, aislamiento del esquema y
preflight.

Tras la corrección de la ronda 1 pasaron 14 pruebas focalizadas y 387
en la suite completa. La prueba nueva reproduce el reinicio tras recibir bytes
parciales y cubre recuperación, agotamiento de reintentos y continuación.

La consulta oficial de lectura de toda la ventana devolvió las
siguientes observaciones por ruta. Estos conteos son del proveedor.

| Indicador | Filas | Primera y última observación | Último valor |
| --- | ---: | --- | ---: |
| EUR/USD | 2012 | 2019-01-02 a 2026-11-09 | 1.1193 |
| Saldo M3 | 93 | 2019-01 a 2026-09 | 17717267 |
| Facilidad de depósito | 21 | 2019-09-18 a 2026-09-16 | 2.5 |
| PIB real | 31 | 2019-Q1 a 2026-Q3 | 0.4 |
| IAPC general | 94 | 2019-01 a 2026-10 | 4.0 |

La primera pasada de ingesta oficial contra la base desechable confirmó
persistencia de 93 saldos M3, 21 eventos de depósito, 31 trimestres de PIB
y 94 meses de IAPC. El evento de depósito del 16 de marzo de 2016 se
persistió aparte como ancla. EUR/USD y la consulta de historial M3
mostraron fallos transitorios de transporte. La repetición de EUR/USD
con el transporte y el adaptador finales recuperó y validó las 2012
observaciones. El ensayo final persistió y leyó las 2251 observaciones
de la ventana, el ancla y una versión de M3. La evidencia de historial
de junio de 2026 contiene la versión `Replace` válida en el repositorio del BCE desde
`2026-10-27T10:00:00.000+01:00` para el valor 17612461 millones de EUR registrado en el contrato de M1.

### Operación DEV y recuperación

La referencia permitida es `swpithkjviavycrxpwed`. El preflight de
solo lectura confirmó el proyecto y la migración pendiente. La aplicación
y la carga son operaciones separadas. Cada operación de escritura exige
la referencia confirmada y el SHA-256 revisado de la migración. El driver
usa la configuración local autorizada, TLS estricto y un ledger dentro
de `macro_data`. No se registran credenciales. Esta sección distingue
los comandos de las operaciones efectivamente realizadas.

```powershell
python -m backend.macro_data.dev --preflight
python -m backend.macro_data.dev --apply --confirm-dev-write swpithkjviavycrxpwed --expect-sha256 <SHA256-revisado>
python -m backend.macro_data.dev --import-history --start 2019-01-01 --end 2026-11-09 --deposit-anchor --confirm-dev-write swpithkjviavycrxpwed --expect-sha256 <SHA256-revisado>
python -m backend.macro_data.dev --status
```

La migración `202611090001` quedó aplicada en DEV con SHA-256
`3ba45046abf339c766838e50fbfcd68e9c518c535955d82e87cf9ae7344b3c44`.
La lectura posterior confirmó los mismos períodos, valores y conteos
de la tabla, más el ancla DFR del 16 de marzo de 2016. Son 2252
observaciones numéricas. Se almacenó además una versión histórica de
M3 separada del conjunto corriente.

Ante una interrupción, comparar el estado con el preflight, ledger y
conteos. Reintentar sólo los indicadores o intervalos fallidos. Mantener
la auditoría independiente y el commit local por etapa. No repetir
operaciones remotas exitosas.

## M3, M4 y M5

M3 autorizado por el PASS de M2. M4 y M5 pendientes de sus etapas
predecesoras. La integración existente conserva sus rutas y comportamiento
mientras se validan los hitos siguientes.
