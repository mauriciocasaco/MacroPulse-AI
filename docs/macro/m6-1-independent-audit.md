# M6.1 — Corrección de alcance semántico y auditoría independiente

La auditoría corresponde a una etapa nueva. Se trabajó en `codex/macropulse-migration`, con HEAD `c36cc26fbb2d68cd306ea87be727034f090383f0` y la referencia local `origin/codex/macropulse-migration` en el mismo commit. El punto de partida incluyó los cambios locales de M6 en el servicio, el corpus y los tests. Se preservaron el corpus y el [informe anterior](m6-independent-audit.md) byte a byte. Se conservaron las correcciones de citas F2, sus pruebas y los diez casos de evaluación M6.

## Alcance del hallazgo F1

La gramática local reconocía un alias de tasas y verificaba palabras contiguas. Si un verbo permitido separaba la autoridad del alias, el grafo podía seleccionar la facilidad de depósito del BCE para otra autoridad. El Coordinador ejecutó el clasificador original en memoria y confirmó la selección `deposit-rate` para Fed, Bank of England y Riksbank, con el mismo resultado esperado para ECB. El [informe M6](m6-independent-audit.md#hallazgo-bloqueante-final) registra la reproducción original en el flujo completo.

## Ronda 1 de 3

El primer intento combinó reconocimiento de autoridades en toda la frase con patrones para sujetos y agentes activos o pasivos. Los casos exigidos para Fed, Reserva Federal, Bank of England y Banco de Inglaterra se abstuvieron. Pasaron 492 pruebas de backend, incluidas 90 focalizadas de Macro AI y los diez casos M6, con PostgreSQL 17/pgvector desechable en loopback, TLS `verify-full` y Gemini simulado. Bandit, Gitleaks con redacción sobre el diff y `git diff --check` pasaron.

El auditor ejecutó el grafo en memoria con el corpus versionado y los fixtures `_histories` y `_draft` de `tests/test_macro_ai.py`. Usó la ventana 2022-01-01 a 2023-12-31 y Python con `-B`. Las siguientes preguntas aún devolvieron `answered` y `series:deposit-rate`.

- «Were interest rates increased during 2022 by Norges Bank?»
- «Did Banxico announce a change in interest rates?»
- «How did Norges Bank decide about policy rates?»

El ejecutor también reprodujo «¿Cómo movió Banxico las tasas de interés?» y «¿Cómo sostuvo Financiera Andina las tasas de interés?». Los complementos y verbos fuera de los patrones permitían saltarse la comprobación de autoridad. Los controles ECB y genérico conservaron el resultado esperado. F2 siguió cerrado.

**Dictamen independiente R1: REJECT, 8,39/10.** Puntuaciones en orden de pesos solicitados: `7,8 × 0,25 + 8,5 × 0,25 + 9,2 × 0,20 + 8,0 × 0,15 + 8,5 × 0,15 = 8,390`. La rúbrica interna obtuvo 8,415 antes de redondear. Quedó un HIGH y cero CRITICAL.

## Ronda 2 de 3

Se sustituyeron los patrones de sujeto por una comprobación positiva de la pregunta de tasas completa. El selector normaliza nombres del BCE, Consejo de Gobierno, eurozona y alias DFR. Conserva palabras desconocidas, incluidas las de un carácter o de otro alfabeto, para poder abstenerse. Tras retirar los alias de tasa, solo admite vocabulario de consulta, evolución, periodo y alcance reconocido. Un posesivo requiere alcance explícito. La comprobación precede a la selección de serie y se aplica exclusivamente a intenciones de tasas.

Pasaron 507 pruebas de backend, incluidas 105 de Macro AI y los diez casos M6. Bandit, Gitleaks con redacción sobre el diff y `git diff --check` pasaron. El auditor ejecutó nueve negativos de autoridad y confirmó que se abstenían, incluidos los cinco casos que fallaron en la ronda 1. También verificó controles BCE, eurozona y DFR, y cuatro casos de PIB o fechas.

El auditor encontró que formulaciones inequívocas como «How high were ECB interest rates?» y «¿Qué hizo el BCE con las tasas de interés?» habían empezado a abstenerse. El Coordinador reconstruyó el selector inicial exacto desde el diff preservado y confirmó la pérdida en esas consultas y en otras variantes sobre el nivel o la evolución de la tasa. La abstención es segura, pero incumplía el requisito de compatibilidad.

**Dictamen independiente R2: REJECT, 8,43/10.** F1 de sustitución quedó cerrado. Quedaron cero HIGH y cero CRITICAL. La pérdida de compatibilidad se clasificó MEDIUM y bloqueó la aceptación funcional. Puntuaciones en orden de pesos solicitados: `8,0 × 0,25 + 8,6 × 0,25 + 9,0 × 0,20 + 8,0 × 0,15 + 8,5 × 0,15 = 8,425`. La rúbrica interna obtuvo 8,42.

## Ronda 3 de 3

Se recuperaron diez formulaciones que el selector de partida aceptaba. Se añadieron palabras funcionales para consultas sobre nivel, máximos y mínimos, magnitud del cambio, disyunción, peticiones corteses y verbos comunes. El filtro de autoridad y el corpus conservaron sus reglas. La implementación está en [service.py](../../backend/macro_ai/service.py#L56) y los controles en [test_macro_ai.py](../../tests/test_macro_ai.py#L261).

| Verificación de la versión final | Resultado |
| --- | --- |
| `python -m pytest -q -rs -p no:cacheprovider` | 517 aprobadas, 0 omitidas |
| `python -m pytest -q tests/test_macro_ai.py -p no:cacheprovider` | 115 aprobadas, incluidos los 10 casos M6 |
| `npm run test` / `npm run test:worker` | 57 / 6 aprobadas |
| `npm run lint`, `npm run typecheck` | Aprobados |
| `npm run build`, `npm run build:public` | Aprobados por defecto y con `MARKETPULSE_MACRO_AI_ENABLED=true` o `MARKETPULSE_DEMO_MACRO_ENABLED=true`, respectivamente |
| Bandit sobre módulos macro, API y origen público | Sin hallazgos medios o altos |
| Gitleaks sobre el diff, con redacción completa, y `git diff --check` | Sin filtraciones ni errores de diff |

El ejecutor corrió el backend en un snapshot temporal del código exacto, con PostgreSQL 17 y pgvector desechables en `127.0.0.1`, TLS `verify-full` y DSN sintéticos. El Coordinador reejecutó el frontend en un snapshot cuyo contenido Git coincide con todos los archivos rastreados de frontend después de normalizar finales de línea.

El auditor ejecutó 13 negativos con el grafo real, corpus versionado, series sintéticas y Gemini simulado. Todos se abstuvieron. Incluyeron Fed y Banco de Inglaterra, autoridades desconocidas, nombres de un carácter o de otro alfabeto, consulta mixta y los fallos residuales de R1. Los controles afirmativos restaurados cubrieron preguntas genéricas y explícitas sobre BCE, ECB, eurozona y DFR. El test de abstención usa funciones que fallan si se invoca la lectura o generación. La suite también volvió a cubrir PIB, IAPC general frente a subyacente, geografías fuera de catálogo, años históricos frente a fechas incompatibles, y la cronología M3–IAPC. F2 conserva las citas documentales aisladas y los cálculos invariantes ante selecciones del proveedor.

Para reproducir el criterio con el entorno de pruebas, ejecutar desde la raíz `python -m pytest -q tests/test_macro_ai.py -k 'rate_authority or supported_rate_questions or contextual_dates or peak_chronology' -p no:cacheprovider`. Para inspeccionar un contraejemplo específico, usar los fixtures `_histories` y `_draft` con `Workflow` y `CorpusIndex.load(CORPUS)`, como se muestra en el informe M6. Con «Were interest rates increased during 2022 by Norges Bank?» y una ventana 2022–2023, la salida esperada es `insufficient_evidence`, evidencia vacía y pasos `interpret → select_indicators → abstain`.

**Límite residual P2.** «¿Qué máximo alcanzaron las tasas de interés del BCE?» se abstiene por el vocabulario acotado, aunque el selector inicial la admitía. La variante inglesa de máximos y mínimos está cubierta. El contrato de reconocimiento sigue siendo conservador. No se verificó compatibilidad exhaustiva con todas las paráfrasis del selector anterior.

| Dimensión | Peso | Nota | Aporte |
| --- | ---: | ---: | ---: |
| Correctitud y fundamentación | 25 % | 8,60 | 2,150 |
| Seguridad, aislamiento y robustez | 25 % | 8,80 | 2,200 |
| Pruebas y regresiones | 20 % | 9,30 | 1,860 |
| Arquitectura y mantenibilidad | 15 % | 8,30 | 1,245 |
| Preparación técnica | 15 % | 8,70 | 1,305 |
| Total | 100 % | | **8,760** |

**Dictamen independiente R3: PASS, 8,76/10.** La rúbrica interna obtuvo 8,815 antes del redondeo, con notas de 8,6 en correctitud, 8,8 en seguridad, 9,3 en pruebas, 8,6 en compatibilidad, 9,0 en alcance y 8,3 en mantenibilidad. Ambos resultados superan 8,50 antes de redondear. F1 y F2 están cerrados. Quedan cero HIGH y cero CRITICAL.

## Preservación y permisos

Se reutilizaron el Coordinador, un ejecutor y un auditor independiente, con participación observada en el host. Modelo/esfuerzo efectivos: no verificables. El rol de auditor declara GPT-6.1 Sol Low. El host informó filesystem irrestricto y aprobación `never`. El auditor ejecutó pruebas en memoria con Python `-B` y fixtures sintéticos. Mantuvo conducta de solo lectura sin aislamiento de escritura impuesto por el host.

Durante las tres auditorías coincidieron HEAD, estado Git, inventario de 149 rutas rastreadas, SHA-256 en memoria de 60 rutas permitidas y diffs exactos. El alcance incluyó el informe M6 no rastreado, pero excluyó los archivos ignorados y las rutas fuera del inventario de hashes. Solo el servicio y sus tests cambiaron respecto del punto de partida de M6.1 antes de escribir este informe. El corpus y el informe M6 mantuvieron sus hashes. No hubo commits, push, despliegue, acceso a Supabase ni invocación de Gemini real.

## Publicación

El gate público sigue abierto. Quedan los [gates DEMO del informe M6](m6-independent-audit.md#gates-de-publicación-demo), la verificación del proveedor real en un entorno autorizado y la revalidación de avisos remotos de dependencias. La aprobación del código no autoriza la publicación.

**M6.1 AUDIT: PASS**\
**SCORE: 8.76 / 10**\
**HIGH/CRITICAL OPEN: 0**\
**M6 CODE GATE: CLOSED**\
**PUBLIC RELEASE GATE: OPEN**
