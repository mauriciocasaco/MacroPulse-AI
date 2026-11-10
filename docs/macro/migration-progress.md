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

## M3 — API y PWA

Recuperación del 10 de noviembre de 2026. La rama estaba limpia en
`aaae7ed015b1740243c3f9bb8b18c1e292c6b52f`, con `dev.py`, sus pruebas
y este registro incluidos en M2. El resultado de la ronda 2 se cotejó
con el informe independiente. Una consulta `--status` de solo lectura
confirmó el ledger y las 2252 observaciones en DEV. La migración y la
carga histórica se conservan.

La API local agrega `GET /macro/indicators` y
`GET /macro/indicators/{id}/history?start=YYYY-MM-DD&end=YYYY-MM-DD`.
La lectura histórica usa `MACRO_API_DSN`, un DSN explícito en loopback
para una transacción de solo lectura, con límites de intervalo y tiempo.
El contrato expone Decimal como texto, períodos originales, estado,
unidad, frecuencia, fuente y hora de captura. La tasa de depósito
distingue eventos, ancla previa y cobertura comprobada.

La PWA local ofrece el espacio Macro mediante `?workspace=macro`. El
gráfico dibuja observaciones por período. Los segmentos de la tasa de
depósito se limitan a fechas con cobertura. La tabla conserva la
precisión original y marca valores ausentes. El área de consulta
en lenguaje natural está prevista para M5. El espacio de precios y
las rutas públicas de 4.4A siguen disponibles con sus contratos.

Verificación local con PostgreSQL 17 desechable: 397 pruebas de
backend, 54 del frontend y 4 del Worker aprobadas. Pasaron el lint y ambos
builds. Vite y Uvicorn en loopback devolvieron el catálogo completo y
datos oficiales de las cinco series desde el proxy normal de la PWA.
La prueba confirmó la diferencia entre el último evento de depósito y
el final de la cobertura. Los reintentos de dos consultas BCE se
resolvieron usando la ventana oficial previamente verificada en M2.

La ronda 1 de auditoría devolvió REJECT (8,83/10). Se cerró el acceso
indirecto desde la LAN en la política del proxy Vite y se comprobó que
las rutas de precios conservan el acceso anterior. La ronda 2 devolvió
PASS (9,55/10), sin hallazgos altos o críticos.
Commit de etapa: `feat(pwa): add macroeconomic indicators and charts`.
La API devuelve el primer tramo continuo de cobertura si existen tramos
disjuntos en una misma selección. La gráfica conserva el hueco.

## M4 — Seguridad de la superficie pública

El acceso macro público requiere `MARKETPULSE_DEMO_MACRO_ENABLED=true`
en el build público, el Worker y FastAPI. El valor predeterminado está
deshabilitado. Los dos endpoints GET usan la misma autenticación de
origen, cuotas y límites de concurrencia de 4.4A. Worker y FastAPI
acotan el intervalo a 2600 días inclusivos. El Worker usa el origen
HTTPS fijo, rechaza solicitudes macro de otro origen y evita
almacenar las respuestas. El service worker excluye toda la API.

`supabase/demo/macro_projection.sql` prepara una proyección vacía
para un DEMO independiente, con SELECT para el rol existente. La
aplicación comprueba catálogo, permisos, columnas y aislamiento de
los esquemas de DEV. La carga futura requiere una extracción revisada
de las cinco fuentes aprobadas, con períodos y escalas originales,
estados confidenciales retenidos como valores nulos y cobertura DFR
verificada. El repositorio no incluye un cargador de DEMO. Las pruebas
usaron una proyección sintética identificada en PostgreSQL desechable.
No se aplicó el SQL en remoto. El análisis público permanece deshabilitado.

Controles alineados con [OWASP API Security Top 10 2023](https://owasp.org/API-Security/editions/2023/en/0x11-t10/):
inventario cerrado de rutas e indicadores, autorización de origen,
proyección de campos y roles separados, límites de consulta y volumen,
cuotas, tiempos máximos y respuestas genéricas. Se probaron accesos
directos sin credencial, métodos y parámetros fuera del contrato,
aislamiento de permisos, agotamiento de cuota y exclusión de caché.
La configuración real de Cloudflare, Render y DEMO queda para una
validación operativa posterior a esta entrega local.

Verificación: 402 pruebas de backend, 54 de frontend, 6 del Worker,
lint y builds de PWA local y pública con ambas configuraciones.
`npm audit` y `pip-audit` finalizaron sin avisos conocidos en las
dependencias resueltas. El primer escaneo de Python detectó cinco
avisos únicos de Starlette 0.52.1. La corrección fijó FastAPI 0.162.2
y Starlette 1.10.0. Se conserva el aviso de obsolescencia de httpx
en TestClient. [Versiones de FastAPI](https://fastapi.tiangolo.com/release-notes/).

`bandit -r backend -f json` detectó el parser SDMX con ElementTree.
Se sustituyó por defusedxml, con rechazo de DTD, entidades y
referencias externas probado. El escaneo final señaló cuatro patrones
en código legado fuera de la superficie pública nueva. El aviso
B608 se cotejó con predicados constantes y parámetros separados. Los
avisos bajos corresponden a un nombre de opción, manejo de interrupción
y una aserción del normalizador. Gitleaks 8.30.1 detectó dos
coincidencias históricas en fixtures sintéticos de 4.4A. El diff de M4
pasó el escaneo de secretos sin coincidencias. Los reportes se
examinaron sin revelar valores.

La auditoría independiente devolvió PASS (9,59/10) en la ronda 1,
sin hallazgos altos o críticos. En M6 se medirá el costo de validar
la proyección y sus permisos por conexión antes de habilitar tráfico.
Commit de etapa: `security(demo): harden macroeconomic public API`.

## M5 — RAG y consultas

M5 autorizado por el PASS de M4.

El endpoint local `POST /macro/analysis` usa el intervalo elegido
en la PWA, lee los indicadores en una transacción de solo lectura y
mantiene la consulta original en el equipo. LangGraph limita el
recorrido a interpretación, selección, lectura, recuperación,
evaluación de suficiencia y generación o abstención. El índice usa
documentos LangChain, fragmentos acotados y vectores TF-IDF con
similitud coseno. Es recuperación léxica y el corpus es deliberadamente
pequeño. La fecha de publicación de cada documento limita su uso
histórico. Las observaciones estadísticas se toman de las versiones
capturadas, con la limitación temporal indicada en la respuesta.

El Runnable de LangChain construye el prompt desde el tema reconocido,
hechos, cálculos y resúmenes públicos. Gemini selecciona referencias
en una salida estructurada. El servidor valida esas referencias y
redacta la interpretación usando los títulos y resúmenes revisados.
Los números y cálculos proceden del backend. Una relación descriptiva
o una correlación no identifica causalidad. El trazado de la cadena
está deshabilitado. Gemini usa una sola solicitud por generación y un
timeout.
[Derechos y atribución del corpus](data-reuse-policy.md#corpus-institucional-de-m5) ·
[Modelo Gemini 3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash).

### Ejecución local

Instalar `backend/requirements-ai.txt` en el entorno local. Proveer
`MACRO_API_DSN` para una base de solo lectura en loopback y la
credencial existente como `GEMINI_API_KEY` por el mecanismo seguro del
entorno. El valor se mantiene del lado del servidor. Activar
`MARKETPULSE_MACRO_AI_ENABLED=true` en Uvicorn y en el frontend local.

```powershell
python -m pip install -r backend/requirements-ai.txt
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
# En otra consola, desde frontend y con el mismo flag local
npm run dev
```

Abrir `http://127.0.0.1:5173/?workspace=macro`. Seleccionar un
intervalo pertinente al episodio antes de enviar la pregunta. El modo
`public_demo`, el Worker y el proxy Vite en modo público carecen de
acceso a esta operación. El servicio web conserva un camino separado
para observaciones públicas macro cuando se autorice DEMO.

Las cinco preguntas del plan se verificaron en el grafo con datos
oficiales en PostgreSQL desechable y un generador simulado. Esa prueba
verificó selección de series, cálculos, contexto y citas. Las respuestas
simuladas se limitaron a las pruebas.

| Pregunta demostrativa | Ventana |
| --- | --- |
| EUR/USD durante la pandemia | 2020-02-01 a 2021-12-31 |
| Inflación tras el inicio de la guerra de Ucrania | 2022-02-24 a 2023-12-31 |
| Cambios de las tasas en 2022 y 2023 | 2022-01-01 a 2023-12-31 |
| Relación entre M3 e IAPC | 2020-01-01 a 2023-12-31 |
| PIB de la eurozona después del COVID-19 | 2020-01-01 a 2023-12-31 |

Por el proxy local de Vite se realizaron tres consultas reales a
Gemini 3.6 Flash con la credencial configurada. EUR/USD y el segundo
intento de M3–IAPC devolvieron 200 con citas. El primer intento de
M3–IAPC devolvió un 503 genérico sin causa confirmada. La respuesta
del segundo intento exageró la separación temporal entre los
máximos de M3 e IAPC. Los datos oficiales de esa ventana los sitúan
en septiembre y octubre de 2022. La versión final descarta la prosa
libre de Gemini y conserva sólo su selección validada de fuentes.
Se reprodujo la salida incorrecta en una prueba local y se volvió a
recorrer el grafo con los cinco ejemplos. Se conservó el protocolo
de llamada y no se hicieron nuevas consultas a Gemini tras la corrección.

El entorno AI resolvió 56 dependencias sin avisos conocidos en
`pip-audit`. El escaneo Bandit del módulo nuevo pasó. Se comprobó
por HTTP que el proxy público rechaza el POST de análisis (404)
y el origen externo obtiene 403 en el servidor local.
Pasaron 417 pruebas de backend, 56 del frontend, 6 del Worker, el
lint y los builds local y público.

La auditoría independiente devolvió PASS (9,51/10) en la ronda 1,
sin hallazgos altos o críticos.
Commit de etapa: `feat(ai): integrate macroeconomic RAG with LangChain and LangGraph`.

## M6 — Rebranding y habilitación

La implementación local y su verificación se registran en el
[informe M6](m6-progress.md). Las tareas de habilitación pública se
mantienen separadas del cambio local.

- Actualizar la marca y el manifiesto PWA, y verificar la transición
  de las instalaciones existentes.
- Preparar y revisar una carga trazable de la proyección en DEMO.
  Validar atribución, permisos, costos de las consultas y límites
  reales del Worker y el origen antes de habilitar tráfico.
- Evaluar una salida Gemini de sólo referencias y repetir una prueba
  real de la versión final con un nuevo presupuesto autorizado.
  Mantener Gemini deshabilitado para visitantes públicos hasta que
  existan cuotas globales verificables y protección contra abuso.
