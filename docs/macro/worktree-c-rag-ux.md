# Worktree C — RAG and LangGraph

Fecha: 2026-11-10. Alcance exclusivo C, un participante, sin auditoría independiente.
Modelo/esfuerzo efectivos: no verificables. Permisos del host: acceso local sin sandbox,
aprobación `never`; no se solicitaron ni eludieron aprobaciones.

Worktree: `C:\Users\Usuario\.codex\worktrees\adf0\MarketPulse-AI`.
HEAD inicial y final: `1829826fdfcbd06023840c50ccd6c0b6adc34d13`, detached HEAD.
El árbol estaba limpio antes del trabajo. Los cuatro checkouts registrados partían
del mismo SHA. Se verificaron los documentos y commits de M6.1, M6.2A y M6.3.
No hubo cambios de rama, staging, commits, merges, push ni deployment.

Se implementaron el título y subtítulo exactos solicitados y tres botones compactos:
COVID-19, Ukraine War y Generative AI. Preparan una pregunta editable con el nombre
amigable del indicador actual. No envían solicitudes; únicamente Ask usa el endpoint
existente. Se conservan consultas libres, fuentes, estadísticas, citas y abstenciones.
El selector comunica su ID a MacroResearch; el ID se convierte al nombre visible al
preparar la pregunta. No se añadió un campo al contrato API: la pregunta enviada ya
contiene el indicador explícito, que el backend valida con sus controles institucionales.

El grafo mantiene sus nodos y validaciones. Solo las envolturas históricas reconocidas
se separan de la pregunta antes de validar el indicador restante. Los eventos añaden
un tema de recuperación y una referencia temporal: 2020-01-01, 2022-02-24 y
2022-11-30, respectivamente. Son referencias de contexto, no estimaciones causales.
Se conserva el período seleccionado y se abstiene antes de acceder a estadísticas
si termina antes del evento. Los documentos deben cubrir el indicador y el evento,
publicarse desde su referencia temporal y no superar el cierre de la consulta.
Sin documentos pertinentes no se invoca Gemini. Las respuestas separan cálculos
descriptivos y contexto documental, con una limitación explícita sobre causalidad.

Archivos modificados:

- `frontend/src/MacroResearch.tsx`: identidad y botones RAG.
- `frontend/src/MacroResearch.test.tsx`: quince combinaciones, cambio de selección,
  edición libre, ausencia de envío automático y envío explícito.
- `frontend/src/MacroWorkspace.tsx`: una línea que pasa `indicatorId={id}`.
- `frontend/src/MacroWorkspace.test.tsx`: conexión real entre selector y botones.
- `backend/macro_ai/service.py`: intención histórica y límites de evidencia.
- `tests/test_macro_ai.py`: matriz histórica, autoridades no admitidas y fechas.
- Este informe, nuevo y sin seguimiento.

Verificación con fixtures sintéticos y proveedor simulado:

- `.\.venv\Scripts\python.exe -m pytest tests/test_macro_ai.py -q`: **151 passed**.
- `npm test` en frontend: **77 passed**, cinco archivos.
- `npm run lint`, `npm run typecheck`, `npm run build` y `npm run build:public`: **PASS**.
- `git diff --check`: **PASS**.
- Regresión ampliada con `tests/test_macro_api.py` y `tests/test_macro_public.py`:
  **173 passed, 3 failed, 3 skipped**. Los tres fallos pertenecen a la configuración
  TLS del lector DEV: falta `Certificate/prod-ca-2021.crt` en este worktree limpio.
  Se reprodujeron los mismos tres fallos cargando en memoria el servicio RAG
  original de HEAD: **19 passed, 3 failed, 2 skipped** en `test_macro_api.py`.
  No se cambió el lector, no se copió el certificado y no se relajó TLS.
  Las pruebas omitidas requieren PostgreSQL local desechable no configurado.

La instalación local utilizó `npm ci --ignore-scripts` y un `.venv` ignorado con
los requisitos AI del repositorio, pytest y httpx. No se modificaron los lockfiles.
No se leyeron archivos .env ni se hicieron llamadas reales a Gemini, Supabase u
otros servicios de aplicación. No se modificaron corpus, credenciales, RLS, datos,
gráficos, navegación legacy ni contratos públicos.

Resultado de las 15 combinaciones con estadísticas sintéticas completas, ventana
2020-01-01 a 2023-12-31 y corpus local actual:

| Indicador | COVID-19 | Ukraine War | Generative AI |
|---|---|---|---|
| EUR/USD rate | Contexto y citas | Abstención | Abstención |
| Eurozone M3 | Contexto y citas | Abstención | Abstención |
| ECB rate | Abstención | Abstención | Abstención |
| Eurozone GDP | Contexto y citas | Abstención | Abstención |
| Eurozone Inflation | Contexto y citas | Contexto y citas | Abstención |

Las quince combinaciones recorren también el grafo completo con un corpus
**sintético separado** que aporta evidencia de cada evento/indicador. Esto prueba
la compatibilidad, sin afirmar que esa evidencia exista en el corpus de producto.
La disponibilidad real depende además de observaciones suficientes y del cierre
temporal. El corpus no se amplió; no contiene material de IA generativa. Los
resultados con contexto tampoco prueban efectos causales.

Se preservaron las regresiones institucionales anteriores y se añadieron 18 casos
de envolturas históricas con Fed, Banco de Inglaterra, PIB estadounidense,
inflación británica y una institución sintética. Todos se abstienen antes de leer
estadísticas o invocar al proveedor. Es evidencia específica contra la atribución
errónea en esos casos, no un cierre global o independiente del hallazgo HIGH.

Para el integrador: trasladar los siete archivos, incluyendo este informe nuevo.
`MacroWorkspace.tsx` y su test pueden coincidir con A/B; conservar el paso del ID y
la nueva prueba al integrar sus cambios de navegación y gráficos. El mapa de
nombres de MacroResearch es local al alcance C y puede consolidarse con el mapa
visual de B sin modificar metadatos institucionales. No se modificaron otros
worktrees. Estado Git final: seis archivos rastreados modificados y este informe
sin seguimiento, sin staging. Pendientes: resolver el fixture TLS de baseline en
la integración, ejecutar la regresión conjunta A/B/C y realizar validación visual
humana. No se inició ningún servidor de aplicación.

WORKTREE C: PARTIAL

RAG EVENT QUESTIONS: 3/3

LANGGRAPH COMPATIBILITY: PASS
