# M7.D.1-FIX — Evaluación de la corrección

La línea base es `b5b8bfae4108076517c7a59da1cc23aa3bd3f739` en `fix/m7d1-evidence-gate`. Las comparaciones usan el mismo corpus editorial M7.B, el mismo modelo E5 local de M7.C y diez repeticiones por consulta. La latencia excluye la carga del corpus, los vectores y el modelo. La cifra de p95 incluye el coste de ordenar cuando el gate invoca el ranker.

## Conjuntos

- **Regresión M7.C.** 28 preguntas originales. Se comparan los resultados del gate y se ejecuta por separado la regresión de recuperación de M7.C.
- **Selección M7.D.1.** 12 preguntas conocidas usadas para comparar rankers.
- **Reserva anterior M7.D.1.** 32 preguntas conocidas para esta corrección. Se usaron para investigar las cuatro falsas abstenciones anteriores.
- **Reserva M7.D.1-FIX.** 17 [juicios](evidence-gate-fix-holdout.jsonl) fijados por el coordinador antes de las ediciones y conservados fuera del checkout hasta la congelación del código. SHA-256 `202825e7a69c189f24207f8181074b0f4216e7987a0084eadc8227a6cded63a3`. El conjunto comparte el corpus aprobado con los anteriores y cambia las preguntas y condiciones. Sus resultados se excluyeron de las decisiones de corrección. El coordinador que preparó el conjunto completó la implementación tras el límite de créditos del ejecutor inicial, por lo que el diseño carece de cegamiento del implementador.

La precisión de grounding cuenta únicamente IDs autorizados mediante `supports_requested_fact`. Los juicios pueden especificar `grounding_relevant_claims` para excluir afirmaciones que solo aportan contexto. Los casos causales tienen cero afirmaciones autorizables. La cobertura de grupos requiere al menos una afirmación autorizada por grupo. La evaluación conserva también la cobertura de selección para las comparaciones históricas.

## Controles de suficiencia

- El gate exige la variante, el ajuste, la unidad y la definición de la medida que corresponden a la consulta. Un nivel, una tasa y el cambio de esa tasa son requisitos distintos. Las variantes sin evidencia aprobada quedan pendientes.
- Un anuncio acredita la decisión y, cuando se explicita, su fecha de vigencia programada. El promedio de la tasa aplicada requiere un promedio publicado con el método y período solicitados o cobertura diaria completa mediante observaciones aprobadas. El promedio derivado pondera los intervalos por días calendario inclusive y conserva sus fuentes. Los huecos, solapamientos, métodos alternativos y publicaciones posteriores al corte impiden la derivación.
- El respaldo de adopción exige el universo de encuesta, el subgrupo, la tecnología y la actividad solicitados. Se preservan los intervalos de edad y los umbrales de empleo disponibles. Un porcentaje marginal no autoriza una intersección de dos fines o tecnologías.
- La cobertura por requisito vincula cada afirmación autorizada con el hecho concreto que respalda. El contexto mantiene su función documental, incluida la evidencia observada en preguntas causales.

## Resultados

Los rankers léxico y E5 coinciden en estados, grounding y cobertura de grupos. Cada celda muestra línea base → corrección.

| Conjunto | Estados correctos | Falsos suficientes | Falsas abstenciones | Grounding correcto / autorizado | Grupos completos |
| --- | ---: | ---: | ---: | ---: | ---: |
| Regresión M7.C aplicada al gate | 26/28 → 26/28 | 0/11 → 0/11 | 2/17 → 2/17 | 16/16 → 16/16 | 1/1 → 1/1 |
| Selección M7.D.1 | 12/12 → 11/12 | 0/3 → 0/3 | 0/9 → 1/9 | 9/9 → 8/8 | — |
| Reserva anterior M7.D.1 | 25/32 → 32/32 | 0/21 → 0/21 | 4/11 → 0/11 | 10/10 → 16/16 | 1/2 → 2/2 |
| Reserva M7.D.1-FIX | 11/17 → 14/17 | 3/14 → 1/14 | 3/3 → 2/3 | 3/4 → 4/5 | 0/1 → 0/1 |

| Conjunto | Gate léxico, mediana / p95 (ms) | Gate E5, mediana / p95 (ms) |
| --- | ---: | ---: |
| Regresión M7.C aplicada al gate | 0,272 / 0,449 → 0,484 / 1,553 | 16,040 / 18,873 → 28,402 / 48,163 |
| Selección M7.D.1 | 0,314 / 0,419 → 0,632 / 1,752 | 17,541 / 21,909 → 32,870 / 54,550 |
| Reserva anterior M7.D.1 | 0,228 / 0,450 → 0,589 / 1,573 | 0,230 / 20,786 → 30,395 / 42,400 |
| Reserva M7.D.1-FIX | 0,191 / 0,494 → 0,573 / 1,316 | 0,200 / 19,977 → 0,684 / 42,537 |

Las latencias son mediciones locales con carga de máquina variable. El aumento del número de consultas con evidencia en la reserva anterior cambia cuántas veces se invoca E5. La regresión M7.C conserva Hit@3 de afirmaciones del gate en 16/17. La ejecución del recuperador híbrido de M7.C obtuvo 17/17 en Hit@3 documental y 12/17 en Hit@3 de afirmaciones verificadas. Pasaron 96 pruebas focales de M7.D.1 y 16 de M7.C con E5. La suite de backend pasó 661 pruebas y omitió 82. Las conexiones de bases de prueba se desactivaron para la suite completa.

La reserva anterior recuperó la comparación de ediciones del PIB, el uso laboral de IA generativa, la categoría combinada de automatización o apoyo a decisiones y la hipótesis atribuida. En selección, `selection-school-ict` ahora se abstiene ante una paráfrasis que ubica a las personas dentro de la encuesta de hogares de Eurostat.

La reserva nueva detectó un falso suficiente en `real-vs-unadjusted`. El intérprete toma «before seasonal adjustment» como una solicitud de ajuste estacional y autoriza la observación desestacionalizada. `separate-person-purposes` se abstiene ante la formulación de la población encuestada. `headline-and-component` conserva la contribución energética y deja pendiente la tasa general cuando el segundo requisito elide el indicador. Estos resultados se registraron con el código congelado.

## Auditoría independiente

El auditor original continuó la revisión con sondas propias, preparadas en memoria. El rol operó en solo lectura. La revisión heredó los permisos amplios del host. Las instantáneas de las dos primeras rondas coincidieron en rama, `HEAD`, referencias, índice, estado Git, inventario, diff y 231 huellas SHA-256. La cobertura excluyó `.env.example`, los metadatos GDELT y los archivos ignorados.

| Ronda | Código | Documentación | Resultado |
| --- | ---: | ---: | --- |
| 1/3 | 8,25 | 8,15 | REJECT. Frecuencia mensual y trimestral, ponderación del promedio, subgrupos de personas y propósito conjunto. |
| 2/3 | 8,475 | 8,15 | REJECT. Diferencias entre tasas, vigencia en la fecha de decisión, población adulta y combinaciones de propósitos. |
| 3/3 | 8,625 | 8,775 | REJECT final. Cero P0 y dos P1 de definición estadística. |

El auditor reprodujo los dos P1 finales con paráfrasis independientes. «What was raw euro area quarterly real GDP growth in Q1 2020 prior to seasonal adjustment?» autoriza la estimación flash ajustada. «How much higher was the October 2022 headline annual HICP rate than in the previous month?» autoriza el nivel interanual como si fuera la diferencia. El resultado es **NO-GO para M7.D.2**. El gate supera el umbral de puntuación de 8,50/10 y los dos P1 impiden su aceptación.

| P1 original | Comprobación final |
| --- | --- |
| Variantes estadísticas | El HICP a impuestos constantes y la tasa neta de impuestos se abstienen con el corpus actual. El criterio general queda abierto por los dos errores de definición estadística reproducidos. |
| Promedios y vigencia | El anuncio del 8 de septiembre queda disponible desde su publicación. El corte del 10 de septiembre bloquea usarlo como observación vigente o promedio mensual. Las sondas sintéticas verifican el promedio por días con cobertura completa y el rechazo de huecos, solapamientos y métodos incompatibles. |
| Poblaciones y denominadores | Las preguntas sobre exportadoras no autorizan la tasa agregada. En preguntas mixtas, esa tasa respalda exclusivamente el requisito que pide la población agregada. Pasaron las sondas de adultos y de propósitos separados frente a un porcentaje combinado. |

En la ronda final coincidieron las 232 huellas cubiertas, los 231 archivos rastreados y los tres archivos nuevos, junto con el estado Git, el diff, el índice y las referencias. Modelo y esfuerzo efectivos sin telemetría verificable.

## Reproducción local

Desde la raíz del repositorio, con el entorno Python y la caché E5 fijada de [M7.C](../m7c/README.md), ejecutar el benchmark con estos pares de argumentos. La opción `--model-cache` se puede omitir para evaluar solo el ranking léxico.

| `--kind` | `--dataset` |
| --- | --- |
| `regression` | `docs/m7c/evaluation.jsonl` |
| `selection` | `docs/m7d/ranker-selection.jsonl` |
| `holdout` | `docs/m7d/holdout.jsonl` |
| `holdout` | `docs/m7d/evidence-gate-fix-holdout.jsonl` |

```powershell
$cache = Join-Path $env:TEMP 'marketpulse-hf-7db692d'
$out = Join-Path $env:TEMP 'marketpulse-m7d1-fix-metrics.json'
python -B -m backend.m7d --model-cache $cache evaluate --kind holdout --dataset docs/m7d/evidence-gate-fix-holdout.jsonl --out $out --repeats 10
```

Para reproducir la línea base con los mismos juicios y las mismas métricas, usar los módulos del commit indicado arriba con `backend/m7d/evaluate.py` de esta corrección. En esta comparación se cargaron los módulos base en memoria mediante `git show`. Cada resultado registra el SHA-256 del corpus, el conjunto y el modelo.
