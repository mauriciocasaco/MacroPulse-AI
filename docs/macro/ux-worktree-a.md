# MacroPulse UX — Worktree A

Fecha: 2026-11-10. Alcance exclusivo A; un participante, sin delegación ni auditoría independiente. Modelo/esfuerzo efectivos: no verificables. Host: acceso al filesystem sin restricciones, política de aprobación never; no se alteró ninguna configuración global.

## Baseline

Worktree: `C:/Users/Usuario/.codex/worktrees/d2fc/MarketPulse-AI`.
HEAD inicial y final: `1829826fdfcbd06023840c50ccd6c0b6adc34d13`, detached HEAD. No se cambió de rama ni se creó un worktree. Estado inicial limpio. La rama local `codex/macropulse-migration` resuelve al mismo HEAD; `main` a `06fcad6`. M6.1, M6.2A y M6.3 están documentados en el baseline; el historial incluye `c36cc26` y `478cd85`. Esto confirma presencia, no una nueva validación de sus conclusiones.

## Resultado

- `frontend/src/App.tsx`: MacroPulse es la única experiencia. Se retiran navegación de workspaces, catálogo de activos, selector de precios y análisis legacy. `?workspace=market` y parámetros de activo antiguos ya no activan Market Archive; se ignoran conservando el enlace. Se preservan los gates públicos macro/IA, reconexión, aviso de actualización PWA y shell offline. Cuando macro no está habilitado se muestra indisponibilidad, sin fallback legacy.
- Eliminados `frontend/src/Analysis.tsx` y `frontend/src/PriceChart.tsx`. Retiradas dos reglas `.macro-nav` de `frontend/src/styles.css`. Las utilidades legacy de datos/presentación y sus pruebas permanecen para evitar una limpieza lateral innecesaria; los componentes retirados ya no forman parte del bundle.
- `frontend/pwa/local-read.ts` y `frontend/vite.config.ts`: se retiran lecturas `/api/assets` y `/api/assets/:id/history`, y POST `/api/analysis` del proxy local. Continúan catálogo/historiales macro y POST `/api/macro/analysis` con sus restricciones previas de loopback, origen y activación. Macro sigue excluido del proxy LAN.
- `frontend/worker/public-demo.mjs`: las rutas legacy devuelven 404 para cualquier método, con macro activado o desactivado, antes de consumir cuerpo, cuotas o contactar al origen. Se conservan allowlists, autenticación al origen, límites, errores sanitizados y no-cache de las rutas macro.
- Pruebas actualizadas: `frontend/src/App.test.tsx`, `frontend/src/MacroWorkspace.test.tsx`, `frontend/pwa/local-read.test.ts`, `frontend/worker/public-demo.node-test.mjs`. Las pruebas de la UI retirada se sustituyen por pruebas de exclusión, enlaces antiguos, feature gates y reconexión. Los controles operativos Worker antes probados sobre assets se prueban ahora sobre rutas macro.

## Límites deliberados

La API backend local y las rutas del origen demo autenticado (`/assets`, `/assets/{series}/history`, `/analysis`) se conservan como infraestructura interna. Ya no se publican a través del Worker ni del proxy Vite. No se afirma que esas rutas hayan sido eliminadas directamente del proceso backend. Una retirada interna posterior requiere revisar consumidores, proyección sintética y pruebas de infraestructura compartida. Ningún endpoint macro cambia de contrato.

No se modificaron gráficos macro, lógica RAG, corpus, datos históricos, tablas, migraciones, políticas RLS, backups ni credenciales. No hubo llamadas reales a Gemini, Supabase u otros servicios de producción; las verificaciones HTTP utilizaron únicamente un origen sintético en loopback. `npm ci --ignore-scripts` instaló dependencias locales fijadas. No hubo commit, staging, push, merge ni deployment.

## Verificación

- Vitest: 41 aprobadas en 5 archivos. Incluye la UI macro real en `/` y `/?workspace=market`, manteniendo pruebas de unidades, decimales, fuentes, observaciones y fechas efectivas.
- Worker: 6 aprobadas. Exclusión legacy sin tráfico al origen, forwarding macro, cuotas, sanitización, timeouts, límites y bypass de caché API.
- Lint, TypeScript, build local y build public-demo: aprobados. Los builds generan la PWA mediante el plugin existente.
- Smoke HTTP de Vite real con origen sintético en loopback: GET/POST legacy 404 sin forwarding; catálogo y los cinco historiales macro 200; análisis macro habilitado 200 hacia el mock; enlace `/?workspace=market` 200. Ambos servidores se cerraron al terminar.
- Backend sin cambios: `MARKETPULSE_DISABLE_LOCAL_DOTENV=1`, `MARKETPULSE_MODE=local`, `python -m pytest -q tests/test_macro_api.py tests/test_public_demo.py tests/test_api.py tests/test_macro_public.py --tb=line`: 77 aprobadas, 3 omitidas, 3 fallidas. Falta `Certificate/prod-ca-2021.crt` en este checkout; fallan `test_remote_reader_preserves_tls_password_and_transaction` y las dos variantes de `test_remote_connection_failure_is_sanitized` con `dev_route_or_tls_config_invalid`. No se añadió ni sustituyó un certificado. Una ejecución inicial con MODE=test produjo además un fallo de configuración esperado en `test_repository_config_forces_tls_verification`; al repetir con MODE=local ese caso pasó.
- `git diff --check`: aprobado. Validación visual humana e instalación/actualización en navegador: pendientes.

## Integración pendiente

Aplicar cambios sin commit incluyendo este documento nuevo y las dos eliminaciones. Revisar solapamientos con B/C en `App.tsx`, `styles.css`, `MacroWorkspace.test.tsx`; conservar de ellos la lógica macro sin restaurar navegación ni fallback legacy. El resto de MacroWorkspace y MacroResearch no se modifica. Revisar también Vite si C necesita ajustes del proxy de investigación.

El integrador debe repetir las suites backend con el certificado CA fijado disponible y DSN desechables para los casos omitidos; ejecutar integración B/C y revisión visual. El hallazgo HIGH sobre atribución institucional está fuera del alcance A y no se declara resuelto por este trabajo. Publicación no autorizada.

Estado Git final: cambios locales sin staging ni commits, en 9 archivos modificados, 2 eliminados y este documento nuevo; ningún cambio backend o tests Python.

WORKTREE A: PARTIAL

LEGACY UI: REMOVED
