# Evaluación offline M7.C

El híbrido encontró un documento relevante en el top 3 en **17 de 17** consultas con respuesta en M7.B. En el primer puesto acertó **15 de 17**, frente a **16 de 17** para BM25, E5 y TF-IDF en el mismo corpus. La afirmación esperada apareció entre los tres primeros documentos en **12 de 17** consultas híbridas y **15 de 17** con TF-IDF. Los resultados dejan pendiente la mejora de la selección de afirmaciones y la abstención antes de una integración.

## Método

Se fijaron [28 consultas y sus juicios](evaluation.jsonl) antes de ejecutar el ranking. Son 17 preguntas con al menos un documento y una afirmación identificados en M7.B y 11 sin evidencia relevante para la pregunta. El conjunto mezcla español e inglés y usa cortes y ámbitos geográficos explícitos. La huella SHA-256 del conjunto es `4e982d228dec55bad829a438110280ef6a9a349495eb3d5d5384bd42a470a79c`. Las [trazas y métricas](metrics.json) registran los resultados por caso, IDs, puntuaciones y muestras de latencia.

BM25, E5, la fusión y la adaptación TF-IDF usan los mismos pasajes de M7.B, filtros y límite de cinco fuentes. La adaptación usa la clase `LexicalEmbeddings` existente y su umbral de coseno de 0,025. Se mide además `CorpusIndex.retrieve` con el corpus actual, sus etiquetas y su límite nativo de tres chunks. Esa prueba refleja diferencias de corpus y filtros además del algoritmo. El recuperador actual tiene material pertinente en dos de las 17 preguntas según los juicios, y recuperó ambos en el top 3. La evaluación nativa carece de una métrica comparable de afirmaciones estructuradas y de Hit@5.

Hit@k mide si aparece al menos un ID relevante antes de k. MRR promedia el inverso del primer rango relevante, con cero si no aparece. La métrica de afirmaciones considera la afirmación seleccionada en cada fuente. La precisión documental cuenta los resultados etiquetados como relevantes para responder cada pregunta entre todos los resultados devueltos. Las preguntas tienen juicios dirigidos a una respuesta concreta, por lo que esta precisión puede penalizar contexto útil. Se midieron tres ejecuciones por consulta en Windows AMD64, Python 3.12.0 y PyTorch CPU con dos hilos. Las latencias incluyen consultas con filtro vacío y el cálculo de la consulta E5 cuando hay candidatos. El arranque del modelo y la carga del índice quedan fuera.

| Método | Hit@1 | Hit@3 | Hit@5 | MRR | Hit@3 de afirmación | Precisión documental | Latencia mediana / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Híbrido BM25 + E5 | 88,2 % | 100 % | 100 % | 0,941 | 70,6 % | 36,2 % | 19,00 / 25,25 ms |
| BM25, M7.B | 94,1 % | 100 % | 100 % | 0,971 | 70,6 % | 38,6 % | 0,05 / 0,12 ms |
| E5, M7.B | 94,1 % | 100 % | 100 % | 0,971 | 82,4 % | 36,2 % | 19,37 / 24,53 ms |
| TF-IDF, M7.B | 94,1 % | 100 % | 100 % | 0,971 | 88,2 % | 39,5 % | 0,50 / 0,95 ms |
| Léxico actual, 2 casos positivos propios | 50,0 % | 100 % | — | 0,750 | — | 28,6 % | 0,08 / 0,14 ms |

El mayor conjunto elegible de este benchmark fue de tres fuentes. Hit@3 y Hit@5 tienen poco poder para distinguir métodos en esas condiciones. El híbrido colocó en segundo lugar la publicación de abril de 2020 sobre energía e inflación y el comunicado de M3 de marzo de 2020. La comparación con los ocho documentos actuales sirve para medir cobertura. El recuperador actual obtuvo 2/17 en Hit@3 al contar todas las preguntas positivas para M7.B.

## Filtros, cobertura y errores

Los 47 resultados híbridos respetaron el corte de publicación, la relación con el indicador, el evento y la geografía. Hubo **cero documentos duplicados** y **cero resultados en los nueve casos con ámbito vacío**. El informe audita la publicación como día inclusive. El caso de hogares en la UE con corte del 15 de diciembre de 2025 excluyó la publicación del día 16, aunque devolvió dos fuentes anteriores de adopción empresarial. La consulta sobre entradas a conciertos recuperó datos agregados del HICP. Los dos casos tenían candidatos elegibles, pero ninguno respondía al propósito específico de la pregunta. Las tres alternativas sobre M7.B también devolvieron resultados en ambos casos.

| Indicador | Consultas positivas | Hit@3 híbrido | Resultados juzgados pertinentes / devueltos |
| --- | ---: | ---: | ---: |
| HICP interanual | 5 | 5/5 | 5/16 |
| PIB real trimestral | 7 | 7/7 | 7/17 |
| M3 | 2 | 2/2 | 2/5 |
| Tasa de depósito BCE | 3 | 3/3 | 3/9 |
| EUR/USD | 0 | — | 0/0 |

En IA, los documentos recuperados se marcan como `proxy`. El control que exige evidencia directa sobre PIB devolvió cero fuentes. Las dos consultas EUR/USD también devolvieron cero fuentes del corpus nuevo. La referencia léxica anterior devolvió un documento sobre liquidez en dólares para la consulta spot de COVID, clasificado como no pertinente para esa pregunta.

## Reproducción y regresiones

La construcción inicial generó **71 vectores E5 de 384 dimensiones**. Dos construcciones independientes con la caché fijada produjeron huellas de vectores idénticas en la prueba de integración. La reconstrucción del índice local reutilizó **71 vectores** y generó **0**. El SHA-256 agregado del índice es `240a646fdb3453c82e2605d8fb56d1a1498a548cf9b986cec01b452b2750b3ba`. Una edición sintética regeneró un único vector. También se comprobaron el rechazo de un índice desactualizado, el rollback y la detección de corrupción.

Pasaron **16 pruebas M7.C** en el entorno E5, incluida la integración con el modelo real. La suite de regresiones en el entorno habitual obtuvo **304 aprobadas y 19 omitidas**. Las omitidas fueron 18 integraciones PostgreSQL local y la integración E5, que pasó en el entorno de inferencia. Se comprobó el comando de búsqueda con una pregunta en español y se revisaron las diferencias de Git. El [manual técnico](README.md) contiene los comandos y el contrato propuesto para M7.D.
