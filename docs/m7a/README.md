# M7.A — Descubrimiento de evidencia histórica

La captura del **11 de noviembre de 2026, 14:26:40 UTC** contiene **122 candidatos institucionales reales** con acceso comprobado en esa corrida. Hay **16 registros GDELT** aparte, **60 documentos propuestos** para revisar en M7.B y **0 aprobados** para ingesta. La corrida hizo 181 solicitudes públicas. Se trabajó sobre `main` en `20503b730cfc357fe2cb5b730793f8ae4060aeba`.

| Evento | Ventana de publicación, inclusiva | Candidatos | EUR/USD | M3 | HICP | PIB real trimestral | Tasa de depósito BCE | Propuesta |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| COVID-19 | 2020-02-01 a 2020-12-31 | 35 | 6 | 16 | 9 | 14 | 12 | 21 |
| Guerra de Ucrania | 2022-02-24 a 2024-02-29 | 69 | 16 | 42 | 27 | 24 | 28 | 23 |
| Auge de la IA generativa | 2023-09-01 a 2026-09-30 | 18 | 1 | 0 | 6 | 17 | 3 | 16 |
| Total | | **122** | 23 | 58 | 42 | 55 | 43 | **60** |

Un documento puede relacionarse con más de un indicador. Los recuentos incluyen referencias de contexto y escenarios. Para Ucrania se usa el 24 de febrero, día de la invasión a gran escala, como límite de publicación. El período estadístico de un dato puede preceder al día del comunicado.

| Institución | Candidatos | Potencialmente reutilizables según política del sitio |
| --- | ---: | ---: |
| Banco Central Europeo | 104 | 57 |
| Eurostat | 11 | 11 |
| Banco de Pagos Internacionales (BIS) | 6 | 0 |
| Junta de la Reserva Federal | 1 | 1 |
| Total | **122** | **69** |

## Entregables y reproducción

- [candidates.jsonl](candidates.jsonl) registra títulos, fechas, institución, URL original y canónica, evento, indicadores, mecanismo, idioma, derechos, estado, relevancia, hashes y localizadores de evidencia. `discovered` indica una entrada documentada sin acceso comprobado; `accessible` exige pasar las verificaciones de la descarga y de la fuente. Las 122 entradas de esta captura son `accessible`.
- [manifest.json](manifest.json) separa los IDs accesibles, los descubiertos sin acceso, los propuestos y los aprobados. También guarda las ventanas, el instante UTC de inicio de la comprobación y hashes de los índices y avisos consultados. `proposal_ids` contiene los 60 IDs en orden de selección; se cruzan con `id` del catálogo.
- [research-seeds.json](research-seeds.json) fija las URLs oficiales revisadas durante la investigación y sus justificaciones. [rejected.jsonl](rejected.jsonl) registra los descartes de la corrida. [gdelt-metadata.jsonl](gdelt-metadata.jsonl) conserva metadatos separados, con URL, fecha de observación, referencia a la fila y hash del archivo GKG.

Desde la raíz del repositorio, con Python 3.12 y las dependencias de desarrollo:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m scripts.m7a_discovery --out .\data\m7a-recheck --with-gkg
python -m pytest -q tests/test_m7a_discovery.py
```

El directorio de salida alternativo conserva la captura entregada para comparación. Omitir `--with-gkg` genera el inventario institucional sin consultar GDELT. Para regenerar los cuatro archivos entregados, usar `--out docs/m7a`. Las URLs canónicas, reglas, ventanas y semillas son reproducibles; los sitios, respuestas HTTP y hashes pueden cambiar. Para comparar dos corridas, cruzar por `id` y comparar `content_sha256`, `publication_evidence` y estado.

El script consulta el [archivo de boletines del BCE](https://www.ecb.europa.eu/press/economic-bulletin/html/all_releases.en.html), los archivos anuales de comunicados y el [archivo de agregados monetarios](https://www.ecb.europa.eu/press/stats/md/html/index.en.html). Descubre recuadros y artículos por el vínculo a su edición y el título del recuadro, y luego comprueba indicadores y términos del evento en la prosa. El [archivo histórico de Eurostat](https://ec.europa.eu/eurostat/web/products-euro-indicators/) aporta comunicados sobre precios y producción; las semillas cubren releases directos aunque falle la paginación. En PDF, los metadatos vienen de la ficha del archivo o de la primera página contrastada en la revisión de las semillas. El script vuelve a validar el tipo y la firma del PDF; no reextrae su dateline.

`id` es SHA-256 de la URL canónica. Se deduplican la URL canónica de destino y el contenido exacto: párrafos normalizados para HTML, bytes para PDF. El hash de PDF detecta copias idénticas, no reexportaciones. `parent_url` identifica boletines que contienen un artículo catalogado aparte. La propuesta evita seleccionar un boletín y su recuadro a la vez. No se comparan similitudes semánticas ni versiones en otros idiomas.

La descarga está limitada a orígenes públicos permitidos sobre HTTPS, con redirecciones del mismo origen, comprobación de tipo MIME, límites de bytes, timeout de conexión y lectura, y un máximo de dos intentos ante fallos de conexión. Se espacian las solicitudes al mismo origen; 429 o 503 lo ponen en pausa durante el resto de la corrida. Se conservan metadatos, hashes y notas analíticas. Los cuerpos institucionales se inspeccionan en memoria y no se guardan.

## Derechos y estados

Los **69 potencialmente reutilizables** tienen evidencia de una política pública aplicable en principio al material institucional. No son aprobaciones de un documento para RAG.

- **BCE:** su [aviso de derechos](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) permite determinados usos con atribución y exactitud. Contiene una excepción para publicaciones con autor, y algunos boletines tienen condiciones propias. Se dejaron pendientes los boletines y análisis firmados. En M7.B, verificar la versión, avisos particulares y material de terceros antes de fragmentar o redistribuir.
- **Eurostat:** su [aviso de reutilización](https://ec.europa.eu/eurostat/help/copyright-notice) identifica CC BY 4.0 para contenido editorial propiedad de la UE. Excluir material de terceros y revisar si hay avisos específicos en cada PDF o página. Conservar la atribución y señalar modificaciones.
- **Reserva Federal:** el [aviso de la Junta](https://www.federalreserve.gov/disclaimer.htm) contempla contenido de dominio público con excepciones. El discurso de 2023 usa la publicación original. Verificar excepciones, citas y figuras antes de aprobar el texto.
- **BIS:** se comprobó el acceso a sus [términos](https://www.bis.org/about/terms-conditions). Las seis publicaciones se mantienen pendientes de comprobar el permiso concreto para almacenamiento, extracción y uso en RAG.
- **GDELT:** se reutilizó el adaptador GKG 2.1 existente para una muestra acotada de un archivo de 15 minutos por evento. Hubo ocho coincidencias para COVID-19 y ocho para Ucrania, ninguna para IA generativa en el archivo muestreado. No se solicitó ninguna URL de un medio. La fecha de observación GKG no acredita la fecha de publicación; si falta título, idioma o fecha de publicación comprobable, el campo queda nulo o se marca como no verificado. Los [términos del proveedor](https://www.gdeltproject.org/about.html) no acreditan permisos del editor. La [documentación de datos GDELT](https://www.gdeltproject.org/data.html) describe los archivos; esta muestra no mide cobertura ni presupone que la API DOC pueda recuperar estas ventanas completas.

## Descartes y cobertura

El registro final contiene **39** incidencias: 21 sin vínculo comprobable con un indicador, 2 sin evidencia temática en la prosa, 14 enlaces de índice fuera de las rutas permitidas y 2 incidencias de acceso a Eurostat (un 429 en el [comunicado de enero de 2024](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-17012024-ap) y la pausa del origen que impidió consultar el archivo en esa corrida). Una corrida anterior pudo consultar el archivo base pero recibió 403 al pasar a la segunda página. El comunicado de enero permanece en las semillas para un intento posterior. La propuesta no depende de esa paginación.

- **COVID-19:** hay cobertura de los cinco indicadores. Los boletines vinculan las necesidades de liquidez y la preferencia por depósitos con el crecimiento de M3. Las decisiones documentan la tasa de depósito y las compras de emergencia; las líneas de liquidez en dólares aportan contexto externo. Los comunicados de Eurostat conservan las estimaciones de PIB y HICP publicadas en ese momento. La publicación del PIB del cuarto trimestre salió después de la ventana.
- **Ucrania:** el BCE documenta transmisión por energía, alimentos, incertidumbre, tipo de cambio y política monetaria. La cobertura de M3 incluye muchos comunicados mensuales con contrapartidas observadas, pero poca identificación del efecto marginal de la guerra. Los comunicados son evidencia de la cifra publicada en su fecha, sujeta a revisión.
- **IA generativa:** hay evidencia microeconómica, datos de adopción y escenarios macro. La [evaluación del BCE de septiembre de 2026](https://www.ecb.europa.eu/press/blog/date/2026/html/ecb.blog20260901~8d48e51f14.en.html) es útil porque no detecta un impulso agregado de demanda de inversión atribuible a IA en la zona euro. El EUR/USD tiene un vínculo indirecto a través de flujos en dólares. Las asociaciones con HICP y la tasa de depósito son escenarios sobre precios y postura monetaria. No se encontró evidencia directa del efecto de la IA generativa sobre M3 ni estimaciones identificadas para el cruce o la tasa del BCE. Los documentos estadounidenses y globales requieren conservar su geografía.

El etiquetado automático es léxico. `mismo_pasaje` indica coincidencia temática y del indicador en un párrafo, no atribución causal. `contexto` separa datos contemporáneos y asociaciones débiles. Las semillas distinguen evidencia directa, canales indirectos y escenarios. Los números de párrafo en `evidence_locator` corresponden a párrafos HTML normalizados de al menos 35 caracteres. Para M7.B, revisar el pasaje señalado y separar los mecanismos del contexto antes de publicar las citas.

## Selección inicial para M7.B

La propuesta de **21 COVID-19, 23 Ucrania y 16 IA generativa** cubre primero los indicadores disponibles y ordena el resto por relevancia estimada, evidencia pública de reutilización, fecha e ID. Limita boletines completos, decisiones y comunicados M3 por evento para que no desplacen análisis o series de Eurostat. La lista es una cola de revisión, con análisis de alto valor cuyos permisos siguen pendientes.

1. Priorizar los comunicados estadísticos y decisiones con base de reutilización comprobada, conservando el estado de la estimación. Para M3 distinguir stock en millones de EUR de la tasa ajustada de crecimiento interanual publicada por el BCE.
2. Revisar los análisis del BCE sobre el vínculo entre el evento y el indicador. Para cada análisis firmado o material BIS, documentar la autorización aplicable o sustituirlo por otro candidato accesible con evidencia suficiente.
3. Mantener separados resultados, escenarios y registros de contexto. Usar los IDs oficiales `eur-usd` (USD por EUR), `m3-stock` (millones de EUR), `hicp-yoy` (% interanual), `real-gdp-qoq` (% trimestral) y `deposit-rate` (% anual). En consultas históricas, no usar textos cuya disponibilidad sea posterior al corte. La fecha publicada tiene precisión de día; no acredita una hora ni garantiza que la página actual sea idéntica a la primera versión.

Verificación local en el entorno del repositorio: **276 pruebas pasaron, 18 omitidas** al ejecutar discovery, piloto de noticias, importación histórica y regresiones de Macro AI. Las pruebas nuevas usan fixtures sintéticos; las omitidas requieren PostgreSQL local. No hubo ingesta, llamadas a Supabase DEV, generación de embeddings ni cambios en archivos del runtime.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_m7a_discovery.py tests/test_news_pilot.py tests/test_historical_import.py tests/test_macro_ai.py tests/test_macro_ai_refinement.py
```
