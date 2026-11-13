# M7.B — Corpus histórico curado

La ampliación M8.5 lleva el corpus vigente a **30 documentos y 53 afirmaciones**. El detalle de fuentes y cobertura está en [M8.5](../m8/corpus-expansion.md). Las cifras de la sección siguiente corresponden a la revisión original.
**24 documentos aprobados y 47 afirmaciones verificadas contra su fuente.** Se revisaron los 60 candidatos de la propuesta M7.A y tres reemplazos del mismo inventario. Todos respondieron y pasaron la comprobación de identidad. No se realizó una búsqueda nueva. Base: `main`, `20503b730cfc357fe2cb5b730793f8ae4060aeba`. Captura de revisión: **11 de noviembre de 2026, 15:52 UTC**.

| Evento | Propuesta M7.A | Reemplazos revisados | Aprobados (incluye reemplazos) | Rechazados | Pendientes |
| --- | ---: | ---: | ---: | ---: | ---: |
| COVID-19 | 21 | 1 | 10 | 4 | 8 |
| Guerra de Ucrania | 23 | 1 | 10 | 2 | 12 |
| Auge de la IA generativa | 16 | 1 | 4 | 1 | 12 |
| Total | **60** | **3** | **24** | **7** | **32** |

En la propuesta original hubo **21 aprobados**, **7 rechazados** y **32 pendientes**. Los tres reemplazos aprobados sustituyen enero por marzo de 2020 para M3, enero por abril de 2022 para M3 y un discurso sobre desequilibrios globales por una publicación Eurostat que mide el uso de IA generativa en hogares. Los comunicados de enero describían observaciones anteriores al shock. Los otros rechazos se deben a anuncios de política con poca información adicional o a una geografía que no corresponde a los indicadores del proyecto. El discurso sobre desequilibrios globales no documentaba el cruce EUR/USD. Cada decisión conserva su motivo y localizador en el registro.

| Evento | EUR/USD | M3 | HICP | PIB real trimestral | Tasa de depósito BCE |
| --- | ---: | ---: | ---: | ---: | ---: |
| COVID-19 | 0 | 2 | 3 | 2 | 4 |
| Guerra de Ucrania | 0 | 3 | 3 | 2 | 3 |
| Auge de la IA generativa | 0 | 0 | 0 | 4 | 0 |

Los recuentos son de documentos y pueden superponerse. Los cuatro vínculos IA–PIB son **proxies**: adopción en empresas y hogares, o un canal de productividad en tareas. No hay una observación aprobada de un efecto de la IA sobre el PIB trimestral ni sobre los otros cuatro indicadores. Tampoco hay evidencia aprobada de EUR/USD en COVID-19 o Ucrania: una fuente pendiente trata el *basis* de swaps, no el spot, y el informe anual que sí comenta el spot tiene condiciones de reproducción más estrechas. Los cinco documentos M3 conservan el crecimiento interanual ajustado publicado por el BCE, no el stock en millones de EUR; su relación con el evento es de contexto. Las series de HICP son tasas interanuales y sus contribuciones están en puntos porcentuales.

## Archivos

- [curated-documents.jsonl](curated-documents.jsonl): 24 documentos, con resumen en inglés de 100–200 palabras, mecanismo, período, geografía, IDs de indicadores, atribución, condiciones y hashes. Solo material aprobado.
- [evidence-claims.jsonl](evidence-claims.jsonl): 47 afirmaciones para los 24 documentos, cada una con localizador, hash del bloque, referencia primaria, fechas, ámbito y calificadores. Contiene 40 observaciones o anuncios constatados, 3 interpretaciones institucionales, 3 expectativas y 1 hipótesis sin confirmar. Un tipo describe lo que afirma el documento, no el nivel de certeza de su interpretación.
- [review-decisions.jsonl](review-decisions.jsonl): los 63 resultados, incluidos los pendientes y rechazados. `replaces` enlaza al candidato rechazado original. Las notas de revisión y los estados de derechos permanecen fuera del corpus publicable.
- [editorial-review.json](editorial-review.json) y [source-checks.json](source-checks.json): insumos reproducibles. El primero registra las decisiones y los resúmenes contrastados con las fuentes. El segundo fija la versión de M7.A, el acceso, el aviso institucional y las huellas de los documentos y de los bloques citados. Las 63 huellas comparables coincidieron con las de M7.A. La captura comprobó cuatro avisos institucionales en 67 solicitudes.

Los JSONL se versionan aquí porque el repositorio ignora `data/`. No se conservaron PDFs, HTML, cuerpos de texto, imágenes, anexos ni contactos. Los IDs de documento se mantienen desde M7.A. Los IDs de afirmación se derivan del documento, su tipo y el texto normalizado. En HTML, el localizador contiene el encabezado y el número de bloque en la extracción de M7.B; en PDF, `pN` identifica la página física. El hash del bloque permite detectar si el pasaje cambió; la revisión de correspondencia entre afirmación y fuente se hizo leyendo el pasaje. Un hash por sí solo no demuestra el significado.

## Derechos y control editorial

Los **12 documentos BCE, 11 Eurostat y 1 de la Junta de la Reserva Federal** se aprobaron para resúmenes atribuidos y hechos estructurados. La aprobación no autoriza incorporar documentos completos ni fragmentos textuales del original. La revisión comprobó el aviso aplicable y la naturaleza del material retenido, excluyendo las partes de terceros.

| Fuente | Alcance de la decisión |
| --- | --- |
| BCE | Decisiones institucionales sin autor individual ni aviso más restrictivo en el documento, y comunicados M3 con permiso de reproducción con atribución en su pie. La [política del BCE](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) distingue material firmado. Los boletines completos y el [informe anual de 2022](https://www.ecb.europa.eu/press/annual-reports-financial-statements/annual/html/ecb.ar2022~8ae51d163b.en.html) incluyen límites propios para reproducción educativa y no comercial; quedan pendientes. |
| Eurostat | Cifras y prosa editorial de la UE verificadas en las publicaciones; [aviso de reutilización](https://ec.europa.eu/eurostat/help/copyright-notice). Se identifica la paráfrasis como modificación, se atribuye a Eurostat y se excluyen imágenes, marcas y material de terceros. |
| Junta de la Reserva Federal | Texto institucional de un discurso original cubierto por su [aviso de dominio público](https://www.federalreserve.gov/disclaimer.htm), salvo excepciones. Las publicaciones de terceros citadas por el discurso no se incorporan como evidencia primaria. |
| BIS | Sus [condiciones](https://www.bis.org/about/terms-conditions) distinguen distribución no comercial y extractos limitados. No se acreditó permiso para el alcance del corpus; no se aprobó ninguno. |

La preparación para M7.C consiste en fragmentar **los resúmenes y las afirmaciones aprobadas**, manteniendo la relación con la fuente, la versión y las condiciones. Para recuperación temporal, usar la fecha de publicación como límite inferior de disponibilidad; el día no demuestra disponibilidad intradía. El período de referencia no es la fecha de publicación. El corpus conserva las estimaciones tal como se publicaron: por ejemplo, una revisión del PIB del primer trimestre de 2020 no debe aparecer en una consulta anterior a la publicación que la incluyó. Los resultados de proyección y las hipótesis deben seguir separados de las observaciones en grounding.

## Reproducir y verificar

Desde la raíz del repositorio, con Python 3.12 en el entorno local:

```powershell
python -m pip install -r requirements-m7b.txt
python -m scripts.m7b_curate build --out data/m7b/rebuilt
python -m pytest -q tests/test_m7b_curate.py
```

`build` no usa la red. Comprueba los hashes de M7.A, identidad y versión de fuentes, derechos, esquemas, duplicados, fechas, parentesco de reemplazos y vínculo entre cada afirmación y su pasaje. Regenera los tres JSONL byte por byte en UTF-8 y LF. El comando falla si un registro pendiente trae contenido publicable o si una aprobación no coincide con la evidencia revisada.

Para repetir la verificación de acceso, contenido y avisos sin cambiar los artefactos aprobados:

```powershell
$replacementArgs = @(Get-Content docs/m7b/review-decisions.jsonl -Encoding utf8 |
    ForEach-Object { ConvertFrom-Json -InputObject $_ } |
    Where-Object replaces |
    ForEach-Object { '--replacement-id'; $_.document_id })
python -m scripts.m7b_curate inspect --out data/m7b/live-inspection.json @replacementArgs
python -m scripts.m7b_curate verify-live --live data/m7b/live-inspection.json
```

La revisión en vivo usa la lista cerrada de M7.A, HTTPS, redirecciones del mismo origen, límites de solicitudes, tipos y tamaños, y pausa ante 429 o 503. Extrae párrafos limpios de HTML o texto por página de PDF, sin OCR ni ejecución de contenido remoto. No hubo fuentes estructuradas adicionales entre los 63 documentos. No se guardan originales ni se sustituyen aprobaciones al volver a consultar. Un cambio en el contenido, archivo o aviso requiere una nueva revisión; la revalidación no confirma automáticamente el significado de una paráfrasis. La coincidencia exacta de texto no detecta paráfrasis o solapamientos parciales; para esos casos se revisaron los anuncios de política en secuencia.

Se ejecutaron **289 pruebas: todas pasaron**, con **18 omitidas** de integración que requieren PostgreSQL local. Incluyen las pruebas M7.B de esquema, extracción, deduplicación, fechas, fuentes, derechos, aislamiento y reconstrucción, más las regresiones de M7.A, noticias, importación histórica y Macro AI. También se ejecutó `git diff --check` y se comprobaron por separado los archivos nuevos. No se cambió el runtime, ni se generaron embeddings ni se escribió en Supabase DEV.
