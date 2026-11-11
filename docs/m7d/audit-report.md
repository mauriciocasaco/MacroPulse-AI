# M7.D.1 — Auditoría independiente

La revisión ELABORADO tuvo tres participantes identificados por el host. El coordinador conservó la línea base y los conjuntos de evaluación, el ejecutor implementó y verificó los cambios y el auditor independiente evaluó el resultado. Modelo y esfuerzo efectivos de los participantes sin telemetría de ejecución verificable. El rol del auditor exige solo lectura. El host heredó permisos amplios y el coordinador verificó la ausencia de escrituras en el alcance cubierto mediante instantáneas.

La rúbrica exige una puntuación ponderada de al menos 8,50/10 antes de redondear, sin hallazgos P0 ni P1 y con evidencia de todas las verificaciones requeridas. La auditoría local no concede autorización de despliegue.

## Ronda 1 de 3

**REJECT.** Código **7,85/10**. Documentación **8,85/10**.

| Peso | Código | Nota | Documentación | Nota |
| ---: | --- | ---: | --- | ---: |
| 25 % | Corrección funcional | 5 | Precisión y consistencia | 9 |
| 25 % | Seguridad y privacidad | 8 | Seguridad y privacidad | 9 |
| 20 % | Pruebas y evidencia | 9 | Evidencia y trazabilidad | 9 |
| 15 % | Compatibilidad | 10 | Viabilidad y compatibilidad | 8 |
| 10 % | Alcance | 10 | Alcance | 9 |
| 5 % | Mantenibilidad | 6 | Claridad y mantenibilidad | 9 |

| Prioridad | Hallazgo reproducido de forma independiente |
| --- | --- |
| P1 | Una consulta de recuento absoluto de empresas se declaró suficiente con un porcentaje. |
| P1 | Una pregunta por dos tecnologías se declaró suficiente con una sola. |
| P1 | Una observación se declaró suficiente en una consulta causal y en otra sobre una previsión. |
| P1 | La geografía nacional o un evento incompatible en el texto pudieron evaluarse con documentos de otro alcance. |
| P2 | Una consulta por la misma medida en dos meses se abstuvo cuando había una afirmación pertinente para cada mes. |

El auditor verificó el checkout, el código, la huella de los tres conjuntos y los recuentos de las trazas. Reprodujo los hallazgos con sondas léxicas de solo lectura distintas de las consultas reservadas. Revisó la evidencia de la integración E5 sin red y las regresiones de Macro AI. La regresión M7.C de esa versión obtuvo 17/17 en Hit@3 de afirmaciones y cero falsas respuestas en 11 negativos. La primera evaluación reservada obtuvo 24/32 estados correctos con tres falsas respuestas y tres falsas abstenciones. Las reproducciones de auditoría se remitieron al ejecutor para corregir el comportamiento sin consultar el conjunto reservado. [Resultados reservados](holdout-report.md)

El coordinador comparó el inventario rastreado de 181 archivos y el inventario no rastreado de 46 archivos, incluidos 30 previos de M7.A–M7.C y 16 de M7.D. Comparó 225 huellas SHA-256 antes y después. No hubo adiciones, eliminaciones, cambios de contenido ni diferencias en el estado Git, el índice, la rama o las referencias locales. La cobertura excluyó `.env.example`, los metadatos GDELT y los archivos ignorados. `main` y `origin/main` apuntaban a `20503b730cfc357fe2cb5b730793f8ae4060aeba`.

## Ronda 2 de 3

**REJECT.** Código **8,20/10**. Documentación **8,85/10**.

| Peso | Código | Nota | Documentación | Nota |
| ---: | --- | ---: | --- | ---: |
| 25 % | Corrección funcional | 6 | Precisión y consistencia | 9 |
| 25 % | Seguridad y privacidad | 8 | Seguridad y privacidad | 9 |
| 20 % | Pruebas y evidencia | 9,5 | Evidencia y trazabilidad | 9 |
| 15 % | Compatibilidad | 10 | Viabilidad y compatibilidad | 8 |
| 10 % | Alcance | 10 | Alcance | 9 |
| 5 % | Mantenibilidad | 6 | Claridad y mantenibilidad | 9 |

Las sondas originales de recuento, evento, geografía, previsión y causalidad explícita pasaron. La consulta de la misma medida en dos meses recuperó las dos afirmaciones. El auditor encontró cuatro P1 con sondas adicionales ajenas al conjunto reservado.

| Prioridad | Hallazgo reproducido de forma independiente |
| --- | --- |
| P1 | Un segundo componente no interpretado se omitió de la evaluación de suficiencia. |
| P1 | Un contrafactual implícito y una pregunta con adopción negada se declararon suficientes con afirmaciones observadas positivas. |
| P1 | Se seleccionó el período citado como límite en consultas que pedían un dato anterior o posterior. |
| P1 | Una tasa agregada se declaró suficiente para un propósito específico o una variante de la medida sin respaldo editorial. |

El auditor verificó la réplica de 25/32 estados, la regresión M7.C de 16/17 en Hit@3 y la selección de 12/12. El ejecutor aportó 50 pruebas aprobadas con E5 local. El coordinador verificó 599 pruebas aprobadas y 82 omitidas con las conexiones de bases de pruebas desactivadas y la integración E5 cubierta en la ejecución específica.

El coordinador comprobó que los 181 archivos rastreados, los 48 no rastreados y las 227 huellas SHA-256 comparadas coincidían antes y después de la ronda. Se conservaron las exclusiones, las referencias y el estado Git de la ronda anterior.

## Ronda 3 de 3

**REJECT final.** Código **8,58/10** con **8,575/10** antes de redondear. Documentación **8,95/10**. El código superó el umbral de puntuación. Tres hallazgos P1 impidieron la aceptación. El auditor no encontró P0.

| Peso | Código | Nota | Documentación | Nota |
| ---: | --- | ---: | --- | ---: |
| 25 % | Corrección funcional | 7 | Precisión y consistencia | 9 |
| 25 % | Seguridad y privacidad | 8,5 | Seguridad y privacidad | 9 |
| 20 % | Pruebas y evidencia | 9,5 | Evidencia y trazabilidad | 9,5 |
| 15 % | Compatibilidad | 10 | Viabilidad y compatibilidad | 8 |
| 10 % | Alcance | 10 | Alcance | 9 |
| 5 % | Mantenibilidad | 6 | Claridad y mantenibilidad | 9 |

| Prioridad | Reproducción e impacto | Criterio pendiente |
| --- | --- | --- |
| P1 | Una consulta por el HICP a impuestos constantes en abril de 2020 recibió el HICP general como evidencia suficiente. | Exigir la variante solicitada o abstenerse. |
| P1 | Una consulta por la tasa de depósito media de septiembre de 2022 recibió la decisión del 8 de septiembre con vigencia desde el 14. El corte era el 10 de septiembre. También se aceptó una tasa nominal para una consulta de tasa neta de impuestos. | Exigir observaciones y cobertura del período para el promedio, y evidencia de la variante para la tasa neta. |
| P1 | En una consulta por adopción de IA entre empresas exportadoras, el estado fue parcial y la tasa de todas las empresas quedó autorizada como grounding. | Usar el subgrupo como restricción del denominador. La tasa agregada solo puede aportar contexto separado. |

El auditor reprodujo los tres hallazgos con consultas ajenas al conjunto reservado y confirmó las correcciones de las sondas de ronda 2. Revisó el resultado de M7.C con 16/17 en Hit@3 de afirmaciones y los dos falsos positivos conocidos corregidos. La última réplica reservada obtuvo 25/32 estados correctos, cero falsas respuestas completas de 21 casos y cuatro falsas abstenciones de 11 casos suficientes. Las sondas del auditor revelaron errores adicionales fuera de esa muestra. [Evaluación reservada](holdout-report.md) · [Regresión M7.C](evaluation-report.md)

El ejecutor verificó 73 pruebas con E5 local sin red y las dos evaluaciones. El coordinador verificó 622 pruebas aprobadas y 82 omitidas, y comprobó los archivos nuevos con `git diff --no-index --check`. Antes y después de la ronda coincidieron los 181 archivos rastreados, los 50 no rastreados, las 229 huellas SHA-256 cubiertas, el estado de Git y las referencias locales. La comparación con la línea base de backend, pruebas y M7.A–M7.C también coincidió en los 104 archivos cubiertos. El auditor ejecutó solo sondas de lectura y revisó las pruebas reportadas.

El límite de tres rondas quedó agotado. M7.D.1 queda como implementación experimental con hallazgos pendientes de aceptación.
