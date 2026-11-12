# M7.D.1-FIX2 — Definición estadística

La revisión parte del checkout `fix/m7d1-evidence-gate` con `HEAD` en `b5b8bfae4108076517c7a59da1cc23aa3bd3f739` y las modificaciones locales de M7.D.1-FIX. La referencia de comparación son las métricas de esa versión. [Informe anterior](evidence-gate-fix-report.md)

## Evidencia y operaciones

- El ajuste estacional, el de calendario, ambos ajustes y la variante bruta se evalúan por separado. Las expresiones negativas de ajuste se interpretan antes de evaluar la polaridad de la afirmación. La ausencia de un calificador de ajuste conserva el comportamiento de la consulta sin ajuste especificado.
- Las comparaciones de tasas usan observaciones con mes y valor explícitos. El cálculo comprueba definición, geografía, unidad, fecha de publicación y corte. Una cifra aislada deja el requisito descubierto. Las comparaciones entre publicaciones con ajuste desconocido o valores en conflicto se abstienen. El cálculo devuelve un valor firmado y la procedencia de sus dos operandos.
- El antecedente de HICP puede completar una tasa interanual elidida cuando la segunda cláusula tiene una formulación reconocible. La segunda cláusula conserva su propia unidad y los dos resultados se vinculan a requisitos distintos.

En la reproducción del BCE y del HICP se usó el corpus aprobado existente. Las pruebas de definiciones incompatibles, geografías distintas y observaciones de publicaciones separadas usan datos sintéticos en memoria. El auditor fijó 16 sondas propias antes de los cambios y las reservó para la revisión del código congelado.

La primera ronda independiente dio **REJECT** con 8,50/10 en código y 8,15/10 en documentación provisional. Las sondas encontraron una negación de ajuste expresada con infinitivo, un cociente tratado como nivel y una pregunta que perdía el nivel solicitado al calcular una diferencia. La corrección posterior reconoce el infinitivo, deja pendiente el cociente y conserva el nivel y la comparación como requisitos independientes.

La segunda ronda dio **REJECT** con 8,50/10 en código y 8,15/10 en documentación provisional. Pasaron las reproducciones de la primera ronda. Las nuevas sondas cubrieron expresiones de corrección por estacionalidad, factores multiplicativos, diferencias absolutas, preguntas por el margen y restricciones de edición. La versión posterior separa la magnitud absoluta de la diferencia firmada y conserva la edición solicitada en una cláusula elidida.

## Resultados de la versión congelada

Los rankers léxico y E5 coincidieron en los estados y en las métricas de grounding. La cobertura de grupos exige afirmaciones autorizadas para cada grupo del juicio. Las comparaciones derivadas se verificaron además con pruebas que comprueban el valor calculado y la procedencia de ambos operandos.

| Conjunto | Estados correctos | Falsos suficientes | Falsas abstenciones | Grounding correcto / autorizado | Grupos completos |
| --- | ---: | ---: | ---: | ---: | ---: |
| Regresión M7.C aplicada al gate | 26/28 | 0/11 | 2/17 | 16/16 | 1/1 |
| Selección M7.D.1 | 11/12 | 0/3 | 1/9 | 8/8 | — |
| Reserva anterior M7.D.1 | 32/32 | 0/21 | 0/11 | 16/16 | 2/2 |
| Reserva M7.D.1-FIX | 16/17 | 0/14 | 1/3 | 5/5 | 1/1 |

| Conjunto | Léxico, mediana / p95 (ms) | E5, mediana / p95 (ms) |
| --- | ---: | ---: |
| Regresión M7.C aplicada al gate | 0,584 / 1,382 | 27,424 / 34,762 |
| Selección M7.D.1 | 0,643 / 1,031 | 30,092 / 35,985 |
| Reserva anterior M7.D.1 | 0,625 / 1,456 | 28,710 / 39,299 |
| Reserva M7.D.1-FIX | 0,537 / 1,147 | 0,560 / 37,092 |

La reserva M7.D.1-FIX pasó de 14/17 a 16/17. Se corrigieron `real-vs-unadjusted` y `headline-and-component`. El caso `separate-person-purposes` sigue pendiente por la formulación de la población encuestada. Los otros tres conjuntos conservaron sus estados caso por caso, incluidas las abstenciones heredadas sobre el primer mes de confinamiento, un recuento de empresas y la paráfrasis de la encuesta de hogares. La comparación de latencia muestra pequeñas variaciones de mediana y p95 en ambos sentidos. Las mediciones incluyen interpretación y ranking y se hicieron bajo carga de máquina variable.

Pasaron 134 pruebas focales de M7.D.1 y 699 en la suite completa, con 82 omitidas. Las conexiones de bases de prueba se desactivaron. La evaluación E5 usó la caché local existente.

## Veredicto independiente

La ronda 3/3 dio **REJECT** con **8,825/10 en código**, **8,775/10 en documentación**, cero P0 y dos P1. Las reproducciones originales y los hallazgos de las rondas 1 y 2 pasaron. La consulta por PIB bruto se abstuvo. La consulta por la diferencia interanual de HICP utilizó dos observaciones aprobadas de septiembre y octubre y produjo 0,7 puntos porcentuales. El auditor reprodujo los resultados de la reserva léxica en memoria y encontró dos problemas adicionales.

| Prioridad | Sonda final | Resultado |
| --- | --- | --- |
| P1 | `What was the euro area HICP index value in October 2022?` | Autoriza 10,6 % interanual como si fuera el nivel del índice. |
| P1 | `Give euro area Q1 2020 quarterly real GDP growth after correcting for seasonal effects but before correcting for calendar effects.` | Pierde el ajuste estacional y la exclusión de corrección de calendario. Autoriza una afirmación que acredita solo el ajuste estacional. |

El auditor también reprodujo abstenciones ante algunas listas y cláusulas elididas con meses distintos y ante una paráfrasis de la población de encuesta de 16 a 74 años. **NO-GO para M7.D.2.**

Las tres rondas conservaron el estado Git, el diff, el índice, la rama, las referencias y las 234 huellas cubiertas de archivos rastreados y nuevos. Quedaron fuera `.env.example`, los metadatos GDELT y los archivos ignorados. El auditor actuó en solo lectura con permisos amplios del host. Modelo y esfuerzo efectivos sin telemetría verificable.

## Reproducción

Desde la raíz, ejecutar las pruebas con las conexiones de bases de prueba desactivadas. Para cada conjunto, ejecutar `backend.m7d evaluate` con los pares de argumentos del [informe anterior](evidence-gate-fix-report.md), con diez repeticiones y la caché E5 local para la comparación semántica. Las latencias excluyen la carga inicial y dependen de la carga de la máquina.

```powershell
python -B -m pytest tests/test_m7d.py -q -p no:cacheprovider
$cache = Join-Path $env:TEMP 'marketpulse-hf-7db692d'
$out = Join-Path $env:TEMP 'marketpulse-m7d1-fix2-metrics.json'
python -B -m backend.m7d --model-cache $cache evaluate --kind holdout --dataset docs/m7d/evidence-gate-fix-holdout.jsonl --out $out --repeats 10
```
