# M8.5 · Informe final de integración

Auditoría local del 13 de noviembre de 2026. Base `107ec6844eaf30f9add5f1ae2863fc525d44e533`, verificada contra `origin/main`. Se usó el checkout existente y se conservaron los cuatro worktrees, todos limpios al recuperarlos.

| Entrega | Rama | Commit recuperado | Merge en `feat/m85-integration` |
| --- | --- | --- | --- |
| Cobertura | `feat/m85-coverage` | `aa726c8` | `99dfe5f` |
| Corpus | `feat/m85-corpus` | `f81a4c9` | `00a3117` |
| Narrativa | `feat/m85-narrative` | `c6f8eea` | `e7699c6` |
| Frontend | `feat/m85-frontend` | `5de9756` | `704b365` |

Los cuatro merges usaron `--no-ff`. En `tests/test_research_v2.py` se resolvió el conflicto conservando las pruebas de elegibilidad del corpus y validando `paragraphs`, el esquema actualizado de composición. Se ampliaron las aserciones de los 15 presets para verificar cobertura, procedencia, unidad y el cierre causal de la narrativa. La revisión de prosa corrigió la expresión inglesa de la unidad de M3 a `million EUR`.

## Matriz de Research v2

Cada celda muestra la unidad del KPI y el número de afirmaciones curadas devueltas por TF-IDF en la base y en la integración. Las ventanas y el corte documental son inclusivos y el corte coincide con el último día de cada ventana. La comparación excluye suplementos online y usa las mismas preguntas y ventanas en las dos revisiones.

| Indicador | COVID-19 · feb–dic 2020 | Ucrania · feb 2022–feb 2024 | IA generativa · sep 2023–sep 2026 |
| --- | --- | --- | --- |
| EUR/USD | % · 0 → 1 | % · 0 → 1 | % · 0 → 0 |
| M3 | % · 1 → 1 | % · 1 → 1 | % · 0 → 0 |
| Facilidad de depósito BCE | pp · 1 → 1 | pp · 1 → 2 | pp · 0 → 0 |
| PIB real trimestral | pp · 1 → 1 | pp · 1 → 2 | pp · 1 → 1 (proxy UE) |
| HICP interanual | pp · 2 → 2 | pp · 2 → 2 | pp · 0 → 0 |

- **15/15 KPI** con insumos sintéticos suficientes, incluido el arrastre de la tasa de depósito con cobertura certificada. El cálculo para niveles mide cambio relativo y el de tasas mide diferencia en puntos porcentuales. El PIB compara tasas de crecimiento trimestral. El HICP compara tasas interanuales.
- **Cobertura curada de 9/15 a 11/15**, con 11 → 15 afirmaciones devueltas al sumar las 15 celdas. Las dos celdas spot que ganaron cobertura curada ya tenían pasajes suplementarios online en la base. La integración reemplaza esos pasajes por actas revisadas y exige una medida bilateral spot. M3 prioriza el stock publicado y Ucrania–tasa y Ucrania–PIB pueden mostrar una segunda observación de otra fuente y período.
- **15/15 respuestas descriptivas parciales** con observaciones, cálculo y una limitación causal explícita. Gemini simulado elige entre estilos y orden de enunciados. El servidor compone observaciones y cambios con sus cifras y fechas y conserva el texto documental aprobado. La narrativa se verificó en inglés con las respuestas de prueba, incluidas las transiciones, las citas y la abstención causal. Los cuatro huecos de IA se expresan como falta de contexto aplicable en el corpus. IA–PIB aporta adopción como proxy de la UE.
- **Citas y compatibilidad.** Las 15 respuestas se comprobaron contra los IDs de evidencia y el catálogo. Para las citas del corpus se cotejaron documento, URL, localizador, derechos, período, publicación, relación y texto aprobado. El cliente TypeScript aceptó las 15 respuestas serializadas y conservó narrativa, fuentes y KPI. Las pruebas de interfaz verifican enlaces, estados parciales y ausencia de datos.
- **Preguntas personalizadas.** Los casos con Alemania, otro indicador, un nivel de índice incompatible, USD o unidad de cambio incompatible se abstienen antes de la lectura. Una solicitud de efecto causal devuelve el límite de identificación y, cuando existe, contexto proxy. Las pruebas de planes alterados cubren cifra, unidad, período, atribución, causalidad y enlaces inventados, con resumen determinista al rechazar el plan.

## Corpus y fuentes incorporadas

El inventario pasó de 24 a 30 documentos, de 47 a 53 afirmaciones y de 71 a 83 pasajes. La reconstrucción offline de los tres JSONL coincide con los blobs de Git tras normalizar finales de línea. El validador aprobó la huella M7.C `ff38a3e14748fbcc3453a46f06de3968429fa9329a619fef3b99d01be6051622`. Se comprobó el corte previo a la publicación, el período íntegro, la exclusión de swaps y la elegibilidad del *flash* de PIB al acotar la consulta al segundo trimestre. La prueba E5 ahora deriva sus conteos del inventario y verificó construcción reproducible, reutilización, invalidación por huella y detección de corrupción con los 83 pasajes.

| Fuente | Afirmación incorporada |
| --- | --- |
| BCE, acta de septiembre de 2020 | Apreciación del EUR/USD de 3,8% entre reuniones, en un contexto de debilidad generalizada del dólar. [Acta](https://www.ecb.europa.eu/press/accounts/2020/html/ecb.mg201008~49aeff32e1.en.html) |
| BCE, acta de abril de 2022 | Movimiento bilateral limitado entre las reuniones de marzo y abril de 2022. [Acta](https://www.ecb.europa.eu/press/accounts/2022/html/ecb.mg220519~c9200dba08.en.html) |
| BCE, anexo monetario de abril de 2020 | M3 de cierre de mes de 13 639 miles de millones de EUR, ajustado. [Anexo](https://www.ecb.europa.eu/press/pdf/md/ecb.md2004_annex~d24fa80d35.en.pdf) |
| BCE, anexo monetario de septiembre de 2022 | M3 de cierre de mes de 16 174 miles de millones de EUR, ajustado. [Anexo](https://www.ecb.europa.eu/press/pdf/md/ecb.md2209_annex~7641617959.en.pdf) |
| BCE, decisión de octubre de 2022 | Alza de 75 pb de la facilidad de depósito, a 1,50% anual desde el 2 de noviembre. [Decisión](https://www.ecb.europa.eu/press/pr/date/2022/html/ecb.mp221027~df1d778b84.en.html) |
| Eurostat, estimación preliminar de julio de 2022 | PIB real de EA19 del segundo trimestre, +0,7% trimestral ajustado estacionalmente en esa edición. [Comunicado](https://ec.europa.eu/eurostat/documents/2995521/14644653/2-29072022-BP-EN.pdf/76432e7c-62cf-653b-6998-195b19b897ee) |

Las decisiones de licencia, atribución y exclusión de candidatos están registradas en la [ampliación del corpus](corpus-expansion.md). Las ediciones EA19 de publicaciones históricas aparecen con su propia geografía y período, separados de la serie EA21. La captura de series puede incluir revisiones posteriores al evento. Una reconstrucción *ex ante* requiere un vintage acreditado para la fecha de corte. [Auditoría de base](coverage-audit.md).

## Quality gate

| Comprobación | Resultado |
| --- | --- |
| `python -m pytest -ra` con PostgreSQL 17, pgvector 0.8.2 y TLS `verify-full` | 952 pasaron. Los cuatro DSN apuntaron a la misma instancia desechable en loopback. |
| M7.C con caché E5 y pesos locales fijados | 16 pasaron en el entorno de inferencia, incluida la prueba omitida en la corrida de PostgreSQL. En conjunto se cubrieron los 953 casos de backend. |
| Frontend y Worker | 153 y 14 pasaron, respectivamente. |
| Lint, TypeScript, builds local y público | Pasaron. |
| Corpus, huella y elegibilidad | Pasaron la reconstrucción offline, la carga por huella y la matriz de elegibilidad. |
| `git diff --check` | Pasó. |
| Gitleaks 8.30.1 con redacción | Sin hallazgos en los commits M8.5. El escaneo histórico de todos los refs señaló 22 coincidencias genéricas, clasificadas como 18 hashes de tokenizadores y cuatro valores de fixtures sintéticos. |

Las pruebas verificaron la admisión por Turnstile antes de alcanzar el origen, cuotas persistentes, fallos de verificación, límites de cuerpo, cancelación y plazos. PostgreSQL se probó con el rol de lectura, intentos de escritura y deriva de permisos. Los cambios conservaron los controles de Worker y los límites de configuración de producción. La auditoría la realizó el agente que integró las ramas. Modelo y esfuerzo efectivos no verificables.

## Validación pendiente en producción

1. Publicar manualmente el commit aprobado en Render según el [procedimiento del entorno](../public-deployment-runbook.md). Verificar arranque y conexión TLS al rol `macropulse_online_ro` de Supabase. Esta entrega no requiere migraciones ni modificaciones de configuración.
2. Compilar el perfil público y publicar manualmente los assets de Cloudflare junto al Worker del commit aprobado. Comprobar el hostname, Turnstile, las cuotas y los errores de timeout desde el flujo del navegador.
3. Verificar las capturas oficiales, sus extremos y revisiones, y probar presets y una consulta editada con Gemini real bajo el presupuesto operativo autorizado. Comprobar la latencia y la experiencia de lectura con las respuestas reales. Esta auditoría usó observaciones sintéticas y simuló Gemini.
4. En instalaciones que habiliten E5, reconstruir el índice con la huella nueva y verificar la recuperación local antes de habilitarlo. El perfil público usa TF-IDF.
