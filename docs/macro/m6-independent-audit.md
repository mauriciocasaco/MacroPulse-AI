# Auditoría independiente de MacroPulse AI M6

Fecha local: 10 de noviembre de 2026. Rama `codex/macropulse-migration`. HEAD auditado: `c36cc26fbb2d68cd306ea87be727034f090383f0`. M6 es `abc0f1c..c36cc26`. La integración M5+M6 se inspeccionó en `14e5a8a..c36cc26`. La referencia local `origin/codex/macropulse-migration` coincidía con HEAD. M4 y la capa de datos se revisaron en los puntos de integración. Este informe conserva [el registro previo de M6](m6-progress.md).

## Arquitectura y contratos observados

- La aplicación local habilita `POST /macro/analysis` mediante bandera, con acceso loopback, validación de Origin/JSON, límite de 4096 bytes, plazo de lectura y un slot por proceso. El frontend separa hechos, cálculos, interpretación y fuentes. Al cambiar el intervalo, vuelve a montar el componente de investigación y cancela la petición anterior. El Worker y el origen público admiten catálogo e historial macro por bandera y excluyen investigación macro.
- El grafo LangGraph es acíclico y tiene límite de recursión 12. Interpreta la pregunta, selecciona series, lee estadísticas, recupera documentos, evalúa suficiencia y termina en respuesta, abstención o error. Los errores del proveedor y borradores inválidos se convierten en 503 sanitizado. Tracing está deshabilitado para el grafo y la llamada.
- La lectura usa DSN loopback y transacción read-only con snapshot repeatable read. Las consultas parametrizan identificadores y fechas, limitan filas, tienen timeouts y excluyen valores confidenciales. El servidor calcula diferencias en unidades de la serie, puntos básicos o puntos porcentuales según corresponda. Pearson usa variaciones interanuales de M3 y meses coincidentes con IAPC, con mínimo de tres pares y sin inferencia causal.
- El corpus editorial local tiene ocho resúmenes, atribución, fechas, enlace institucional y política de reutilización. TF-IDF, etiquetas y fecha de publicación acotan candidatos. Gemini recibe un plan canónico, estadísticas y contexto público, y el servidor valida los IDs. La pregunta libre se queda local. La revisión de derechos existente se inspeccionó en el repositorio sin consultar los sitios de origen. [Política de reutilización](data-reuse-policy.md).

## Ronda 1 de 3

### Pruebas ejecutadas sobre el commit

El ejecutor usó snapshots temporales del commit y PostgreSQL 17/pgvector desechable con puerto aleatorio en `127.0.0.1`, TLS `verify-full` y CA sintética. Las variables `MARKET_DATA_TEST_DSN`, `MACRO_DATA_TEST_DSN`, `MARKET_API_TEST_DSN` y `MARKETPULSE_NEWS_LOCAL_DSN` apuntaron al mismo servicio desechable. Gemini fue simulado.

| Comprobación | Resultado |
| --- | --- |
| `python -m pytest -q -rs -p no:cacheprovider` | 427 aprobadas, 0 omitidas |
| `python -m pytest -q tests/test_macro_ai.py -p no:cacheprovider` | 25 aprobadas, incluidos los 10 casos de evaluación |
| `npm test` y `npm run test:worker` desde `frontend/` | 57 y 6 aprobadas |
| `npm run lint`, `npm run typecheck`, `npm run build`, `npm run build:public` | Aprobados. Builds adicionales con IA local y catálogo macro público activados por separado |
| `python -m bandit -q -ll -r backend/macro_ai backend/macro_data backend/api.py backend/public_demo.py` | Sin hallazgos de severidad media o alta |
| `gitleaks git --redact=100 --log-opts=14e5a8a..c36cc26 --no-banner` | Sin filtraciones detectadas, dos commits |
| `git diff --check 14e5a8a..c36cc26` | Sin errores |

Los diez casos ejercitan el grafo con el corpus real y series sintéticas. Ambos miembros de cada fila pasaron. La fuente esperada se valida entre los candidatos, mientras el doble de Gemini elige el primer candidato. La última columna muestra lo que efectivamente citó el doble.

| Original y paráfrasis | Serie esperada | Fuente esperada recuperada | Fuente seleccionada |
| --- | --- | --- | --- |
| EUR/USD en pandemia / euro frente al dólar en coronavirus | EUR/USD | BCE, liquidez USD, marzo de 2020 | Misma fuente |
| Inflación europea tras invasión / precios al consumidor | IAPC | BCE, Ucrania, marzo de 2022 | Misma fuente |
| Tasas 2022–2023 / ECB interest rate | Facilidad de depósito | BCE, política, julio de 2022 | BCE, política, septiembre de 2023 |
| M3 e inflación / broad money y consumer prices | M3 e IAPC | BCE, evolución monetaria, diciembre de 2020 | Eurostat, IAPC de octubre de 2022 |
| PIB tras COVID / gross domestic product tras pandemia | PIB real de zona euro | Eurostat, PIB T2 de 2020 | Misma fuente |

### Hallazgos y contraejemplos

| ID | Severidad | Evidencia y efecto en el commit | Corrección propuesta |
| --- | --- | --- | --- |
| F1 | HIGH | [service.py:53](../../backend/macro_ai/service.py#L53) despacha por precedencia y trata `tasa` como facilidad de depósito. Se ejecutó «¿Cómo cambió la tasa de desempleo?» y se obtuvo DFR. «Compare EUR/USD with inflation in 2022» devolvió solo EUR/USD. «How did GDP change in France after COVID?» devolvió el agregado de la zona euro. En los tres casos el estado fue `answered`. | Reconocer candidatos antes de despachar, abstenerse de comparaciones y geografías incompatibles, y restringir los alias de DFR. |
| F2 | HIGH | [retrieval.py:118](../../backend/macro_ai/retrieval.py#L118) exige la primera etiqueta. [models.py:75](../../backend/macro_ai/models.py#L75) valida membresía y tipo, pero [service.py:95](../../backend/macro_ai/service.py#L95) rotula todos los documentos elegidos con todas las series elegidas. Con `Workflow`, fixtures de `tests/test_macro_ai.py` y selección M3+IAPC+swap USD, la respuesta presentó la medida de liquidez USD como contexto de M3 e IAPC. El documento estaba entre los tres recuperados. | Ajustar las etiquetas del corpus y citar resúmenes editoriales por separado, sin convertir agrupaciones de Gemini en afirmaciones sobre series. |
| F3 | MEDIUM | [service.py:181](../../backend/macro_ai/service.py#L181) usa las fechas de la solicitud y el corte superior de publicación. EUR/USD «en 2023» con campos del 2020 devolvió `answered` con datos del 2020. El intervalo sí aparece en la respuesta. | Abstenerse ante contradicción inequívoca con fechas explícitas. |
| F4 | MEDIUM | [statistics.py:71](../../backend/macro_ai/statistics.py#L71) declara el uso de revisiones locales disponibles. La evidencia de serie omite fecha de captura y revisión. El repositorio admite `known_at` y expone primera/última captura, pero el API de historial llama sin corte y omite esos datos en las filas. Inspección estática corroborada por fixtures con captura en 2026 para periodos 2020–2023. | Exponer metadatos de la versión y diseñar un modo histórico explícito si se requiere reconstrucción point-in-time. |
| F5 | MEDIUM | [statistics.py:80](../../backend/macro_ai/statistics.py#L80) permite contestar con dos eventos DFR aunque falte cobertura continua. Los extremos y deltas se refieren a puntos observados, pero la evidencia muestra el intervalo primero–último sin representar huecos. La nota sobre el final del primer tramo se omite si la cobertura es nula. Inspección estática. | Mostrar la falta de cobertura continua junto a hechos o exigirla para respuestas sobre el intervalo completo. |
| F6 | MEDIUM | Un resumen sintético sobre catering con URL institucional y etiquetas `fx/covid` alcanzó el umbral y se citó. Las etiquetas curadas se incluyen en ambos vectores, y el umbral léxico no certifica pertinencia. No se observó tal entrada en el corpus de producción versionado. | Revisar contenido y etiquetas juntos al cambiar el corpus. No aceptar material externo sin curación. |

La paráfrasis difícil «single currency against the greenback» se abstuvo, un límite seguro de cobertura. Las pruebas incluyeron ausencia de evidencia, documentos posteriores al corte, IDs inventados, prosa no permitida, errores del proveedor, salida malformada e instrucciones dentro de un documento. Las instrucciones no lograron introducir IDs externos. F2 demuestra que ese control de pertenencia por sí solo no evita relaciones incorrectas entre IDs válidos. El caso de cronología M3–IAPC cubrió septiembre frente a octubre con series sintéticas y distintas selecciones. Los valores de una edición institucional pueden diferir de la versión actual o sintética de la serie.

| Dimensión solicitada | Peso | Nota R1 | Aporte |
| --- | ---: | ---: | ---: |
| Correctitud funcional y fundamentación | 25 % | 6,00 | 1,500 |
| Seguridad, aislamiento y robustez | 25 % | 8,50 | 2,125 |
| Pruebas y regresiones | 20 % | 8,00 | 1,600 |
| Arquitectura y mantenibilidad | 15 % | 8,00 | 1,200 |
| Preparación técnica del código | 15 % | 8,00 | 1,200 |
| Total | 100 % | | **7,625 → 7,63** |

**Dictamen independiente R1: REJECT.** Dos HIGH y cero CRITICAL. El auditor aplicó también la rúbrica interna y obtuvo 7,65/10, por debajo de 8,50 antes de redondear. Los defectos son de selección y fundamentación pese al buen resultado de las suites. La preparación se valoró por los contratos y guardas locales. Los gates de publicación se tratan aparte.

La ronda congeló la revisión con estado limpio antes y después. Coincidieron HEAD, los 149 archivos del inventario Git, diff bruto y estadístico M5+M6, y SHA-256 en memoria de 59 rutas permitidas. Quedaron fuera archivos ignorados y rutas ajenas al inventario de hashes. No hubo escrituras del auditor.

## Ronda 2 de 3

Se modificaron localmente `backend/macro_ai/service.py`, `backend/macro_ai/corpus.json` y `tests/test_macro_ai.py`. Los cambios separan la cita documental de las cifras calculadas, quitan etiquetas que asociaban documentos con indicadores sin soporte, detectan intenciones reconocidas en paralelo y acotan tasas de depósito, calificadores directos de PIB y años o fechas ISO discordantes. La comparación M3–IAPC conserva el documento monetario pertinente. La regresión compara dos selecciones válidas y exige hechos y cálculos idénticos. El contexto documental lleva la fecha de su propia edición.

Pasaron 434 pruebas de backend sin omisiones, incluidas 32 de Macro AI y los 10 casos M6. Bandit y Gitleaks sobre el diff pasaron. `python -m pip check` y `npm ls --depth=0 --offline` verificaron consistencia local. Las suites y builds de frontend y Worker siguen siendo aplicables porque sus archivos coinciden con la ronda 1. Durante la revisión coincidieron HEAD, estado de tres rutas, diff local exacto, inventario de 149 rutas y hashes de las mismas 59 rutas.

**Dictamen independiente R2: REJECT.** F2 quedó cerrado. F1 siguió HIGH y el número de CRITICAL fue cero. La inspección de `_gdp_scope_supported` e `_interpret` mostró que los calificadores antepuestos, como «French GDP» o «France's GDP», y los adjetivos pospuestos, como «PIB francés», eluden el control. «French inflation», «US interest rates» e «inflación subyacente de la zona euro» todavía seleccionaban sustitutos de catálogo según la misma inspección. El control de fecha también podía confundir un año de contexto histórico con el periodo pedido.

Puntuación solicitada, en el mismo orden de dimensiones: `7,0 × 0,25 + 8,5 × 0,25 + 8,5 × 0,20 + 8,0 × 0,15 + 8,5 × 0,15 = 8,05`. Rúbrica interna: `8,075 → 8,08`. Ambos valores quedaron por debajo de 8,50.

## Ronda 3 de 3

La revisión final del mismo diff local aplica una gramática conservadora de calificadores adyacentes a PIB, IAPC, M3 y DFR. Las pruebas cubren formas en inglés y español, variantes posesivas y adjetivales, alcance nacional, inflación subyacente, iniciales con puntos, alias válidos de BCE/zona euro y abstención antes de lectura o generación. Las referencias históricas inequívocas a una crisis anterior ya no invalidan el intervalo consultado. Permanecen acotados el análisis de fechas y la cobertura lingüística. La separación de documentos y series de la ronda 2 se mantuvo.

Pasaron **458 pruebas backend sin omisiones**, incluidas **56 de Macro AI y los 10 casos M6**. La ejecución reutilizó PostgreSQL loopback, TLS, pgvector y Gemini simulado. Bandit, Gitleaks con redacción en el diff local y `git diff --check` pasaron. La revisión congelada preservó HEAD, 149 rutas del índice, 59 hashes permitidos, estado de tres modificados y diff local exacto.

### Hallazgo bloqueante final

**F1 · HIGH · La autoridad monetaria explícita puede quedar fuera del alcance reconocido.** [`_scope_supported`](../../backend/macro_ai/service.py#L82) examina las palabras contiguas al alias. En «How did the Fed change interest rates?», el verbo `change` es un prefijo admisible y la autoridad queda fuera de esa inspección. [`_interpret`](../../backend/macro_ai/service.py#L173) toma la rama DFR. Se reprodujo con tres consultas, las dos sobre la Reserva Federal en inglés y español y otra sobre el Banco de Inglaterra. Las tres devolvieron `answered` con `series:deposit-rate` del BCE. Las consultas equivalentes con BCE y ECB devolvieron correctamente esa serie.

Reproducción desde la raíz con el entorno de pruebas local y los tres archivos de la ronda 3. El siguiente ejemplo carga los fixtures del repositorio, usa el corpus de la copia local y simula Gemini.

```python
from runpy import run_path

fixture = run_path("tests/test_macro_ai.py")
service = fixture["service"]
corpus = fixture["CorpusIndex"].load(fixture["CORPUS"])
workflow = service.Workflow(corpus, read=fixture["_histories"], generate=fixture["_draft"])
for question in (
    "How did the Fed change interest rates?",
    "¿Cómo cambió la Reserva Federal las tasas de interés?",
    "How did the Bank of England change interest rates?",
):
    result = workflow.run(fixture["_request"](question, "2022-01-01", "2023-12-31"))
    print(result.status, [item.id for item in result.evidence if item.kind == "series"])
```

Resultado observado: `answered ['series:deposit-rate']` para cada pregunta. Resultado esperado: abstención antes de consultar estadísticas o Gemini. La corrección mínima pendiente es evaluar autoridades monetarias explícitas en toda la intención de política antes de aplicar el alias DFR y abstenerse cuando la autoridad reconocida sea incompatible o ambigua. Debe incluir controles BCE en ambos idiomas y variantes con el sujeto separado del alias por un verbo.

F2 permanece cerrado para el corpus versionado. Siguen pendientes la trazabilidad de vintages de F4 y la presentación de huecos de DFR de F5. F6 delimita la confianza en el corpus editorial versionado. La inspección identificó también falsos negativos como «Did inflation accelerate?» y «What was the euro area's real GDP growth in 2020?», por adyacencias no previstas. Esos ejemplos son inspecciones estáticas. La gramática favorece abstención en formulaciones desconocidas.

| Dimensión solicitada | Peso | Nota final | Aporte |
| --- | ---: | ---: | ---: |
| Correctitud funcional y fundamentación | 25 % | 7,30 | 1,825 |
| Seguridad, aislamiento y robustez | 25 % | 8,50 | 2,125 |
| Pruebas y regresiones | 20 % | 9,00 | 1,800 |
| Arquitectura y mantenibilidad | 15 % | 8,00 | 1,200 |
| Preparación técnica del código | 15 % | 8,50 | 1,275 |
| Total | 100 % | | **8,225 → 8,23** |

**Dictamen independiente R3: REJECT.** La rúbrica interna obtuvo 8,25/10. Ambos resultados quedan por debajo de 8,50 antes de redondear. Hay un HIGH y cero CRITICAL. Se alcanzó el máximo de tres rondas. Quedan en el checkout las correcciones locales de las dos iteraciones y este informe. HEAD sigue siendo el commit auditado. La mejora de citas pasó la revisión. El fallo de autoridad sigue impidiendo el cierre del código.

## Gates de publicación DEMO

- Autorizar y cargar una proyección aislada de Supabase DEMO con datos de reutilización permitida, procedencia y fechas de captura. Verificar catálogo, grants mínimos, ausencia de rutas a DEV y separación de credenciales.
- Configurar el origen Render y el Worker Cloudflare. Verificar autenticación Worker/origin, rechazo de acceso directo, límites desplegados, exclusión de caché API y comportamiento PWA en un navegador instalado.
- Mantener Gemini deshabilitado para visitantes públicos hasta contar con controles de abuso, presupuesto global, concurrencia, monitorización y mecanismo de apagado. La compatibilidad del modelo y del esquema con Gemini real queda pendiente de una prueba local separada y autorizada.
- Los advisories vigentes de dependencias no se volvieron a consultar en esta auditoría local. Bandit y Gitleaks cubren riesgos distintos. Se inspeccionaron los metadatos y el registro de reutilización versionados.

## Ejecución y permisos

Se observó un Coordinador, un `ejecutor` y un `auditor` en el mismo chat Desktop, reutilizados para las rondas. La plataforma registró las altas y los estados de los tres participantes. No se usó el launcher CLI ni un worktree. Modelo/esfuerzo efectivos: no verificables. El rol de auditor declara GPT-6.1 Sol Low. El host informó filesystem irrestricto y aprobación `never`. El auditor mantuvo comportamiento de solo lectura comprobado por los snapshots dentro de su alcance. El host no impuso aislamiento de escritura y no se hicieron pruebas de permisos mediante escrituras.

La auditoría usó fixtures sintéticos y dejó la publicación pública abierta. No hubo llamadas reales a Gemini, cambios a DEV/DEMO, commits, push ni despliegue.

**M6 AUDIT: REJECT**  
**SCORE: 8.23 / 10**  
**HIGH/CRITICAL OPEN: 1**  
**M6 CODE GATE: OPEN**  
**PUBLIC RELEASE GATE: OPEN**
