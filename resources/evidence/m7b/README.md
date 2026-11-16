# M7.B — Corpus histórico curado

El corpus vigente contiene **30 documentos y 53 afirmaciones aprobadas**.
La cobertura, las fechas de publicación, las unidades, el ámbito y los
calificadores se registran por afirmación. El corpus incluye evidencia
contextual sobre EUR/USD, anexos del saldo M3 y decisiones del BCE, y
estimaciones del PIB de Eurostat. La evidencia sobre IA generativa incluye
medidas de adopción y productividad en tareas con alcance limitado.

## Archivos

- [Documentos](curated-documents.jsonl) y [afirmaciones](evidence-claims.jsonl) aprobados, con IDs, atribución, metadatos y huellas.
- [Decisiones editoriales](review-decisions.jsonl), incluidos los candidatos rechazados y pendientes.
- [Revisión editorial](editorial-review.json) y [captura de verificación](source-checks.json), con la procedencia de las aprobaciones y las huellas de los documentos, los bloques citados y los avisos de reutilización.
- [Entradas de descubrimiento](../m7a/README.md) y [candidatos de ampliación](expansion-candidates.jsonl) para reconstruir el conjunto revisado.

## Derechos y control editorial

Los **17 documentos BCE, 12 Eurostat y 1 de la Junta de la Reserva Federal** se aprobaron para resúmenes atribuidos y hechos estructurados. La aprobación no autoriza incorporar documentos completos ni fragmentos textuales del original. La revisión comprobó el aviso aplicable y la naturaleza del material retenido, excluyendo las partes de terceros.

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
$replacementArgs = @(Get-Content resources/evidence/m7b/review-decisions.jsonl -Encoding utf8 |
    ForEach-Object { ConvertFrom-Json -InputObject $_ } |
    Where-Object replaces |
    ForEach-Object { '--replacement-id'; $_.document_id })
python -m scripts.m7b_curate inspect --out data/m7b/live-inspection.json @replacementArgs
python -m scripts.m7b_curate verify-live --live data/m7b/live-inspection.json
```

La revisión en vivo usa la lista cerrada de M7.A, HTTPS, redirecciones del mismo origen, límites de solicitudes, tipos y tamaños, y pausa ante 429 o 503. Extrae párrafos limpios de HTML o texto por página de PDF, sin OCR ni ejecución de contenido remoto. No se guardan originales ni se sustituyen aprobaciones al volver a consultar. Un cambio en el contenido, archivo o aviso requiere una nueva revisión; la revalidación no confirma automáticamente el significado de una paráfrasis. La coincidencia exacta de texto no detecta paráfrasis o solapamientos parciales; para esos casos se revisaron los anuncios de política en secuencia.
