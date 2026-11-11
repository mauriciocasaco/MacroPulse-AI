# Evaluación offline de M7.D.1

## Diagnóstico

M7.C mezclaba pasajes de resumen y afirmación al ordenar documentos. Cada rama aportaba el mejor pasaje por documento a la fusión, por lo que un resumen podía colocar bien una fuente aunque el pasaje describiera otro predicado. El selector posterior elegía una sola afirmación por fuente a partir de posiciones relativas de similitud. Así podía perder un segundo hecho del mismo documento o elegir una tasa en vez de una contribución en puntos porcentuales. En los casos sin respuesta, un documento del mismo ámbito podía obtener puntuación por vocabulario compartido y superar los filtros de M7.C. Esto ocurrió con los precios de entradas de conciertos y la adopción en hogares antes de publicarse la encuesta.

M7.D.1 separa los filtros editoriales, la cobertura de predicados y la ordenación. Preserva la fecha de referencia y la publicación, así como el tipo y los calificadores de la evidencia. Una afirmación de aportación contable de energía se separa de la tasa de inflación energética. La adopción en empresas, el uso por personas y los indicios sobre productividad de tareas se conservan como medidas propias. Los datos de adopción siguen siendo evidencia indirecta respecto al PIB. Una decisión de tasa anunciada, su entrada en vigor y una intención futura tienen predicados distintos.

Las pruebas sintéticas incluyen preguntas por componentes coordinados, períodos discretos con una edición solicitada, ventanas temporales ambiguas, tasas ajustadas, poblaciones restringidas, no adopción y contrafactuales. Por ejemplo, para «energía y servicios públicos» se entrega solo la contribución de energía con estado `partial`. Para un contrafactual de inflación se conserva la tasa observada como contexto sin atribuir el efecto. Una tasa agregada de adopción carece de cobertura para el uso exclusivo en contratación.

## Selección del ordenador

Se compararon dos configuraciones fijas sobre [12 preguntas etiquetadas por el coordinador](ranker-selection.jsonl), distintas de las 28 de M7.C y de la validación reservada. Las preguntas comparten el corpus de 24 documentos y 47 afirmaciones. Se priorizó evitar falsas respuestas, acertar estados y preservar precisión y Hit@1/3 de afirmaciones. La latencia decide cuando las alternativas empatan en calidad. Los cuatro experimentos conservan los mismos filtros de ámbito. El léxico usa coseno TF-IDF sobre enunciados y el semántico coseno E5 multilingüe sobre los mismos enunciados, sin fusión ni umbral de suficiencia.

| Método | Documento Hit@1/3 | Afirmación Hit@1/3 | Precisión de afirmaciones | Estados correctos | Falsas respuestas / abstenciones |
| --- | --- | --- | --- | --- | --- |
| TF-IDF solo | 8/9 · 9/9 | 4/9 · 9/9 | 9/54 | — | — |
| E5 solo | 8/9 · 9/9 | 7/9 · 7/9 | 9/54 | — | — |
| Gate + TF-IDF | 9/9 · 9/9 | 9/9 · 9/9 | 9/9 | 12/12 | 0/3 · 0/9 |
| Gate + E5 | 9/9 · 9/9 | 9/9 · 9/9 | 9/9 | 12/12 | 0/3 · 0/9 |

Ambas estrategias con gate empataron en la evaluación de selección. Se fijó TF-IDF por menor latencia y disponibilidad sin pesos de modelo. La mediana y el p95 fueron 0,33 y 2,12 ms con TF-IDF, frente a 17,62 y 20,40 ms con E5. La [traza de selección](ranker-selection-metrics.json) conserva los resultados y latencias por consulta.

## Regresión M7.C

| Método | Documento Hit@1/3 | Afirmación Hit@1/3 | Precisión de afirmaciones | Negativos con resultados |
| --- | --- | --- | --- | --- |
| M7.C híbrido | 15/17 · 17/17 | 11/17 · 12/17 | 12/47 | 2/11 |
| M7.C BM25 | 16/17 · 17/17 | 11/17 · 12/17 | 12/43 | 2/11 |
| M7.C E5 | 16/17 · 17/17 | 13/17 · 14/17 | 14/47 | 2/11 |
| M7.C TF-IDF | 16/17 · 17/17 | 14/17 · 15/17 | 15/41 | 2/11 |
| M7.D.1 TF-IDF solo | 16/17 · 17/17 | 14/17 · 17/17 | 18/82 | 2/11 |
| M7.D.1 E5 solo | 15/17 · 17/17 | 14/17 · 17/17 | 18/82 | 2/11 |
| M7.D.1 Gate + TF-IDF | 15/17 · 16/17 | 15/17 · 16/17 | 17/18 | 0/11 |
| M7.D.1 Gate + E5 | 16/17 · 16/17 | 16/17 · 16/17 | 17/18 | 0/11 |

Los dos métodos con gate acertaron 26 de 28 estados, con cero falsas respuestas en los 11 negativos y dos abstenciones según los juicios originales de M7.C. La consulta `covid-hicp-energy` describe el primer mes de confinamiento amplio sin dar una fecha. El gate devuelve dos candidatos de meses distintos como contexto y se abstiene de dar una respuesta completa. E5 ordena primero el candidato juzgado relevante. La consulta `genai-adoption-es` pide cuántas empresas usaban IA y está etiquetada con una proporción del 8 %. El gate exige un conteo y devuelve `insufficient`. Se conservaron la pregunta y el juicio originales en el cálculo. La consulta `covid-hicp-november` selecciona las dos afirmaciones del mismo documento, cada una con su medida y cita. Los dos negativos con ámbito elegible terminan en `insufficient` sin afirmaciones.

| Indicador | Casos | Hit@3 de afirmaciones, Gate + TF-IDF | Estados correctos |
| --- | ---: | ---: | ---: |
| HICP interanual | 8 | 5/5 | 7/8 |
| PIB real trimestral | 11 | 6/7 | 10/11 |
| M3 | 3 | 2/2 | 3/3 |
| Tasa de depósito BCE | 4 | 3/3 | 4/4 |
| EUR/USD | 2 | — | 2/2 |

Por evento, los estados correctos fueron 10/11 en COVID, 9/9 en Ucrania y 7/8 en IA generativa. La mediana y el p95 fueron 0,30 y 0,97 ms con Gate + TF-IDF y 16,19 y 20,23 ms con Gate + E5. La [traza de regresión](metrics.json) incluye Hit@1/3, precisión, latencia y tasas de error por evento e indicador y por consulta.

## Medición y límites

Hit@k usa los casos con juicios de relevancia y exige al menos un ID relevante entre los primeros k resultados. Para documentos se deduplican los IDs en orden de aparición. La precisión divide las afirmaciones juzgadas pertinentes por las afirmaciones devueltas, incluido el contexto. La precisión de grounding aplica ese cálculo solo a afirmaciones con `supports_requested_fact`. La regresión observó 16/16 afirmaciones de grounding pertinentes en ambos métodos con gate. Una falsa respuesta es un estado `sufficient` en un caso cuyo estado esperado es otro. Una falsa abstención es cualquier otro estado cuando el esperado es `sufficient`. Los métodos sin gate registran resultados en negativos y carecen de estados evaluables.

Se ejecutaron tres repeticiones por consulta en Windows AMD64, Python 3.12 y CPU con dos hilos para E5. La latencia por consulta incluye alcance, inferencia cuando procede y selección. Excluye carga del corpus, inicialización del modelo y construcción de vectores. El conjunto conocido comparte corpus, filtros e intenciones con el desarrollo. La comparación demuestra los resultados sobre esos juicios. El [informe de validación reservada](holdout-report.md) documenta la evaluación independiente.
