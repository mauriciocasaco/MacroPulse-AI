# Fase 12.S2 · Verificación

Base `b6e6abde261ae14d29085aae6dd63e9003e8f6a2` de `origin/dev`, con PR #34 y los reintentos breves de Flash-Lite de la PR #40. Véase el [contrato y Gate A](fase-12-s2-contract.md). Los resultados de este documento corresponden a fixtures sintéticos y servicios locales. La observación de checks remotos corresponde al SHA publicado en la PR.

## Worktrees

| Worktree | Rama | Commit de la tarea | Verificación |
| --- | --- | --- | --- |
| A | `fix/fase-12-s2-contract` | `c2cf4dc` | Contrato, catálogo y fixtures compartidos |
| B | `fix/fase-12-s2-admission` | `e618136` | Admisión en Demo y Online. Pruebas de rechazo, consumo y ausencia de egreso |
| C | `fix/fase-12-s2-pure` | `e22b33e` | Autorización de fallback, selección acotada y pruebas de respuesta pública |
| D | `fix/fase-12-s2-client` | `e5ad626` | Worker, navegador y validación del catálogo |
| E | `fix/fase-12-s2-integration` | Commit que incorpora este informe | A–D incorporados por cherry-pick. La integración adapta el parser simulado de las pruebas 12.S1 a una pregunta admisible y conserva los vectores firmados |

## Matriz adversarial

| Entrada o condición | Antes | Resultado observado |
| --- | --- | --- |
| Pregunta económica con orden de ignorar instrucciones, etiqueta de desarrollador, orden de herramienta o separador Unicode | El texto podía llegar al intérprete | 422 de política tras consumo, con cero llamadas SQL o Gemini. Repetir el mismo cuerpo firmado devuelve 403 |
| Credencial con formato reconocible, credencial configurada o dato excesivo | Había comprobaciones repartidas entre origen y proveedor | 422 fijo. Los tests verificaron la ausencia de egreso y confirmaron el rechazo de credenciales configuradas |
| Salto de línea en la pregunta | El esquema del transporte ya lo rechazaba | 422 técnico, sin trabajo posterior |
| Intent con operación, serie, unidad, medida, geografía, extracto o fecha modificados | Una insuficiencia podía habilitar el generador | Ocho mutaciones conservaron el diagnóstico RAG y ninguna produjo una selección conceptual |
| Observaciones sin cobertura, error SQL, fallo de interpretación | El resultado dependía del diagnóstico que alcanzaba el grafo | La ausencia de observaciones sigue siendo 200 insuficiente. Los fallos técnicos siguen siendo 503/504 según su tipo y plazo. Se conservó Flash → Flash-Lite y la política Lite → Lite |
| “Inflation increased by several percentage points”, causalidad confirmada, hallazgo atribuido al BCE/Eurostat, instrucciones o HTML | Se evaluaba prosa libre mediante patrones | Los 13 fixtures adversariales de salida fueron rechazados. Ninguna prosa aportada por el proveedor apareció en la respuesta conceptual |
| ID ajeno, inventado, duplicado, fuera de orden o ancla ausente | El fallback carecía de inventario de fragmentos | La selección falla con `invalid_response`. Se conserva el cuerpo RAG completo, salvo el registro del intento |
| JSON inválido o duplicado, `MAX_TOKENS`, bloqueo de seguridad, timeout o caída del proveedor | Podía faltar la explicación | Seis casos técnicos retuvieron el cuerpo RAG y registraron fallo tipado sin texto del proveedor |
| 422 de origen con HTML, exceso de tamaño, campos adicionales o sin confirmación de finalización | El Worker devolvía un 422 técnico genérico | Solo la respuesta 422 JSON completada con el código acordado atraviesa el Worker. Lee a lo sumo 256 bytes y devuelve un cuerpo fijo. Conserva el lease si el origen carece del marcador |
| Respuesta conceptual adulterada, cambio de pestaña, RAG válido | El navegador verificaba patrones sobre prosa | El cliente exige igualdad con el catálogo y omite un fallback inválido preservando RAG. El navegador comprobó las dos vistas con una sola solicitud de Research |
| Replay, HMAC adulterado, rotación, scope, callback y límites de reserva | Protecciones 12.S1 | Suite Python y Worker aprobadas. El flujo local con workerd, Durable Object SQLite y FastAPI ejercitó consumo único |

## Utilidad y latencia

- De 19 preguntas del conjunto de contrato, se admitieron 10. Ocho eran conceptuales y dos eran descriptivas. Las ocho preguntas conceptuales cubiertas devolvieron una explicación pertinente de catálogo con un proveedor simulado. Ejemplos representados incluyen inflación y sus canales, política monetaria, tipo de cambio, depósito del BCE, M3, PIB real y transmisión a la inflación.
- Hubo nueve rechazos en ese conjunto. Uno fue un falso rechazo de utilidad por falta de cobertura, «How does the Phillips curve work?», entre nueve preguntas conceptuales legítimas evaluadas. Los ocho rechazos restantes cubrieron una entrada ambigua, una ajena al dominio y seis casos de control, dato sensible o forma inválida. El conjunto es dirigido y pequeño.
- Una explicación aceptada usa un preflight y una generación, con cero lecturas SQL. En la prueba de reloj sintético equivalente de Demo, el camino anterior hacía cuatro operaciones de proveedor y el actual hace dos. Con 12 segundos simulados por operación, la prueba pasó de 48 a 24 segundos simulados. Los rechazos hacen cero invocaciones al proveedor. La clasificación local midió una mediana de 13,81 µs por pregunta en siete lotes de 1.000 recorridos de los 19 fixtures.
- Las dos evaluaciones de latencia usan simulación o CPU local. La latencia, la disponibilidad y la tasa de selección de Gemini real quedan pendientes de un entorno autorizado.

## Pruebas y herramientas

| Verificación | Resultado observado |
| --- | --- |
| Python + PostgreSQL 17 desechable con pgvector y TLS + Miniflare local | 1.079 pruebas distintas aprobadas en dos perfiles de conexión, una omitida. La ejecución con TLS tuvo 1.077 aprobadas y dos fallos de la API local por su rechazo deliberado de un DSN con TLS. El módulo local completo pasó 24/24 al usar su perfil `sslmode=disable` |
| Reproducibilidad E5 real | Una omitida por ausencia de `M7C_TEST_MODEL_CACHE` y de la pila local de inferencia. Se ejecutaron las pruebas de ranking y corpus que usan fixtures |
| Backend compileall, TypeScript y oxlint | Aprobados |
| Vitest, Worker, navegador Edge | 165, 48 y 3 aprobadas respectivamente, sin omitidas |
| Build local y público | Ambos aprobados. Vite informó un bundle de océano superior a 500 kB |
| Auditorías de dependencias | `pip-audit` en los dos grafos de CI y `npm audit` en producción y árbol completo terminaron sin vulnerabilidades conocidas |
| Gitleaks local | Diff de la rama contra `origin/dev` sin hallazgos, escaneado con redacción |
| Benchmarks lexicales de Release Quality | Regresión 28 casos, cobertura de afirmaciones en top 3 de 17/18. Holdouts de 32 y 17 casos. Cero respuestas falsas en los tres |

El primer ensayo con un PostgreSQL sin TLS ni pgvector falló en los fixtures que requieren esas capacidades. Se repitió con el contenedor y los perfiles anteriores. Dos pruebas 12.S1 requerían que el parser simulado devolviera una pregunta que pudiera llegar al runner. Se ajustó el mock y se repitieron sin cambios a los bytes firmados ni a las expectativas de atestación.

## Riesgos y límites

La política reconoce una capacidad acotada de entrada. Puede rechazar vocabulario, fuentes, formulaciones y escrituras macroeconómicas legítimas. Las preguntas descriptivas admitidas todavía llegan como datos al intérprete. La extracción de intención puede equivocarse dentro del ámbito que validan los controles RAG existentes. La detección de secretos cubre formatos y coincidencias conocidas. El catálogo revisado y la comprobación de inventario del servidor impiden que prosa arbitraria del proveedor llegue al campo conceptual. La precisión económica del catálogo depende de su revisión y la representación pública conserva `verified: false`.

El runtime de Cloudflare, la integración con el servicio Gemini y el entorno de Render se probaron indirectamente mediante workerd y transportes sintéticos. La prueba operativa y la revisión independiente quedan pendientes. Una PR con checks satisfactorios aporta evidencia del cambio de código. La mitigación en producción depende de promoción y despliegue deliberados.

## Activación y reversión

1. Revisar los checks del SHA exacto y promover manualmente por `dev → staging → prod` según las reglas de la rama. Verificar la convivencia del frontend, Worker y servidor durante la transición. Un cliente nuevo conserva RAG al recibir el esquema conceptual anterior.
2. En el entorno autorizado confirmar primero el 422 genérico, la liberación del lease, la separación de scopes y un RAG descriptivo válido. Confirmar un ejemplo de cada concepto y que el cliente conserva el RAG al cambiar de vista.
3. Usar el flag existente `MACROPULSE_RESEARCH_GENERAL_FALLBACK_ENABLED=true` para habilitar el selector. Quitar el flag o establecerlo en `false` deshabilita las explicaciones conceptuales y conserva la admisión y RAG. Para revertir todo el release, deshabilitar primero ese flag y volver a una revisión aprobada con las protecciones 12.S1.
