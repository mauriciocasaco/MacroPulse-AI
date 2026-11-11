# M7.D.1 — Evaluación reservada

El coordinador fijó 32 preguntas y sus juicios antes de inspeccionar la implementación. El ejecutor recibió el esquema y el [conjunto de selección del ranker](ranker-selection.jsonl). El conjunto reservado se incorporó al checkout después de que el ejecutor congeló el código y las pruebas. La huella SHA-256 de los [juicios reservados](holdout.jsonl) es `de73e50428c1f9d08a805cbdd88ff3e8f1e87148d208520056b8e8f6666b2158`.

Las preguntas comparten el corpus M7.B y sus eventos con la regresión. Cambian la formulación y las condiciones solicitadas. Se anotaron 11 casos suficientes, 3 parciales, 15 insuficientes y 3 de causalidad sin identificación. En 16 preguntas hay al menos una afirmación pertinente, incluida evidencia contextual que puede citarse por su contenido observado. Dos preguntas requieren más de una afirmación. El alcance es documental y las pruebas de movimientos oficiales usan series sintéticas.

## Primera ejecución congelada

La huella de los archivos Python de esta versión fue `55e04e2b122b3afd16257ea544930a66968b14d4534ffb99089894e19e389568`. Se calculó a partir del manifiesto de huellas SHA-256 individuales de `backend/m7d/*.py`, ordenado por ruta. Cada línea contiene la huella en minúsculas, dos espacios, la ruta relativa y un salto LF.

| Método | Documento Hit@1 / Hit@3 | Afirmación Hit@1 / Hit@3 | Precisión de afirmaciones | Mediana / p95 |
| --- | ---: | ---: | ---: | ---: |
| Híbrido M7.C | 16/16 · 16/16 | 12/16 · 12/16 | 14/69 | 17,47 · 22,21 ms |
| TF-IDF M7.C | 13/16 · 15/16 | 11/16 · 12/16 | 14/59 | 0,49 · 0,71 ms |
| Léxico por afirmación | 15/16 · 16/16 | 13/16 · 16/16 | 21/121 | 0,13 · 0,23 ms |
| E5 por afirmación | 15/16 · 16/16 | 14/16 · 16/16 | 21/121 | 21,43 · 26,96 ms |
| Gate léxico | 11/16 · 11/16 | 11/16 · 11/16 | 13/16 | 0,22 · 0,57 ms |
| Gate E5 | 11/16 · 11/16 | 11/16 · 11/16 | 13/16 | 0,25 · 25,72 ms |

M7.C ordenó documentos y conservó como máximo una afirmación por documento. Los métodos de M7.D.1 ordenaron afirmaciones y deduplicaron documentos para Hit@k documental. Los dos métodos M7.C devolvieron candidatos en 13 de los 15 casos insuficientes. El gate léxico autorizó 11 afirmaciones pertinentes entre 12 afirmaciones para grounding y cubrió todos los grupos requeridos en uno de los dos casos multiafirmación.

| Estado esperado | Suficiente | Parcial | Insuficiente | Causalidad sin respaldo |
| --- | ---: | ---: | ---: | ---: |
| Suficiente | 8 | 0 | 3 | 0 |
| Parcial | 2 | 0 | 1 | 0 |
| Insuficiente | 1 | 0 | 14 | 0 |
| Causalidad sin respaldo | 0 | 0 | 1 | 2 |

Los dos rankers con gate produjeron la misma matriz. Hubo 3 falsas respuestas completas entre 21 preguntas sin respaldo completo y 3 falsas abstenciones entre 11 preguntas suficientes. Los falsos positivos incluyeron una proporción que se tomó por un recuento absoluto y dos preguntas en las que faltaba una de las medidas solicitadas. Entre las abstenciones hubo una comparación de dos versiones del PIB, un uso de IA por propósito y una hipótesis atribuida. Los resultados por indicador, evento y consulta están en las [métricas de la primera ejecución](holdout-metrics.json).

El ranking léxico encontró al menos una afirmación pertinente en el top 3 en 16 de las 16 consultas con evidencia anotada. Con el gate, esa cifra bajó a 11 de 16. La diferencia localiza el principal límite en la interpretación y comprobación de la pregunta. El gate aumentó la precisión y redujo la cobertura en este conjunto pequeño de consultas sobre el mismo corpus histórico de la regresión.

## Repetición tras la primera auditoría

Se repitieron los mismos juicios después de las correcciones motivadas por sondas independientes del auditor. Esta repetición mide el cambio sobre las 32 preguntas originales.

| Estado esperado | Suficiente | Parcial | Insuficiente | Causalidad sin respaldo |
| --- | ---: | ---: | ---: | ---: |
| Suficiente | 7 | 1 | 3 | 0 |
| Parcial | 1 | 1 | 1 | 0 |
| Insuficiente | 0 | 0 | 15 | 0 |
| Causalidad sin respaldo | 0 | 0 | 1 | 2 |

Las dos variantes del gate acertaron 25/32 estados, con una falsa respuesta completa y cuatro falsas abstenciones. La respuesta errónea restante cubrió una contribución al HICP y omitió un segundo componente pedido en la misma pregunta. Los aciertos documentales y de afirmación en top 1 y top 3 fueron 11/16. La precisión de afirmaciones seleccionadas fue 12/14 y todas las autorizadas para grounding fueron pertinentes. El gate léxico tuvo 0,26 ms de mediana y 0,67 ms en p95. [Trazas de la repetición](holdout-after-audit-1.json)

## Repetición tras la segunda auditoría

| Estado esperado | Suficiente | Parcial | Insuficiente | Causalidad sin respaldo |
| --- | ---: | ---: | ---: | ---: |
| Suficiente | 7 | 1 | 3 | 0 |
| Parcial | 0 | 1 | 2 | 0 |
| Insuficiente | 0 | 0 | 15 | 0 |
| Causalidad sin respaldo | 0 | 0 | 1 | 2 |

La última versión obtuvo 25/32 estados correctos y cero falsas respuestas completas entre 21 casos sin respaldo completo. Persistieron cuatro falsas abstenciones entre 11 casos suficientes. La contribución al HICP que antes se declaró suficiente pasó a parcial. Otro caso de adopción con dos tecnologías pasó de parcial a insuficiente al aumentar las restricciones de interpretación.

| Variante final | Documento Hit@1 / Hit@3 | Afirmación Hit@1 / Hit@3 | Precisión de afirmaciones | Mediana / p95 |
| --- | ---: | ---: | ---: | ---: |
| Gate léxico | 10/16 · 10/16 | 10/16 · 10/16 | 11/13 | 0,29 · 1,24 ms |
| Gate E5 | 10/16 · 10/16 | 10/16 · 10/16 | 11/13 | 0,34 · 30,91 ms |

Las dos variantes conservaron una precisión de grounding del 100 % sobre las afirmaciones que autorizaron y cubrieron todos los grupos en uno de los dos casos que requerían varias afirmaciones. Las limitaciones de cobertura incluyen preguntas que comparan ediciones del PIB, usos concretos de IA, una hipótesis atribuida y respuestas parciales sobre períodos distintos. [Trazas finales por indicador, evento y consulta](holdout-after-audit-2.json)

Por evento, los estados correctos fueron 9/11 para COVID, 12/13 para Ucrania y 4/8 para IA generativa. Por indicador, fueron 8/8 para HICP, 4/4 para M3, 4/4 para la tasa de depósito, 2/2 para EUR/USD y 7/14 para el ámbito de PIB, que incluye las consultas sobre proxies de IA.

## Reproducción

Desde la raíz del repositorio, con el entorno de M7.C y su caché E5 local, ejecutar el benchmark con tres repeticiones por consulta. La latencia excluye la carga inicial del corpus, vectores y modelo.

```powershell
$cache = Join-Path $env:TEMP 'marketpulse-hf-7db692d'
$out = Join-Path $env:TEMP 'marketpulse-m7d-holdout-metrics.json'
python -B -m backend.m7d --model-cache $cache evaluate --dataset docs/m7d/holdout.jsonl --kind holdout --out $out --repeats 3
```

Para obtener las filas de referencia de M7.C se proyectaron las mismas 32 preguntas al esquema `backend.m7c.evaluate.Case` y se ejecutó el evaluador M7.C con el índice local existente y tres repeticiones. El cálculo de precisión contó las afirmaciones identificadas en las trazas y los aciertos usaron los mismos IDs de los juicios reservados.
