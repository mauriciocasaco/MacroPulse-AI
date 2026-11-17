# Fase 12.S4 — Verificación

Base remota confirmada en `eabb01452444abdcf7cbd4f10541598e24fc5736`, merge de 12.S3 en la PR #44. La integración se hizo por cherry-pick. El checkout original de `prod` conservó su edición de `frontend/wrangler.jsonc`.

| Worktree | Commit de trabajo | Commit en integración |
| --- | --- | --- |
| A · Contrato y fixtures | `600018c` | `2ed4a2f` |
| B · Admisión | `f6a2dab` | `7a967e9` |
| C · Worker y cliente | `5e0d114` | `5eec803` |
| D · Integración workerd | `2624974` | `2624974` |

## Matriz de ataques

| IDs | Categoría | 422 de intención | 422 de datos sensibles |
| --- | --- | ---: | ---: |
| 01–10 | Manipulación de instrucciones | 10 | 0 |
| 11–20 | SQL | 10 | 0 |
| 21–30 | Código y comandos | 9 | 1 (24) |
| 31–40 | Extracción | 10 | 0 |
| 41–50 | Entradas mixtas y evasión | 9 | 1 (47) |
| **Total** | | **48** | **2** |

Cada ataque se ejecutó en FastAPI DEMO con scope público y en FastAPI Online con scopes público y administrativo. Los 150 envíos firmados obtuvieron el cuerpo fijo, `Cache-Control: no-store` y la marca de finalización. Se verificó 403 sin firma y al repetir cada envío. Los mocks impidieron el ingreso al runner, cualquier conexión SQL de investigación y cualquier transporte Gemini, incluido `countTokens`. El fallback Pure vive detrás del runner.

Las 30 preguntas legítimas llegaron intactas a un runner sintético. Las pruebas existentes cubrieron RAG, Pure y el failover Flash → Flash-Lite con mocks. El ensayo local de workerd conectó el Worker y su Durable Object SQLite a FastAPI, verificó ambos 422 y rechazó la repetición de los bytes firmados. Los tests del Worker comprobaron la liberación del lease al recibir una respuesta completada y su retención cuando falta la marca.

## Comprobaciones

- Backend. `compileall` correcto. 1.309 pruebas aprobadas entre la suite completa y las repeticiones dirigidas, con el modelo opcional M7C omitido. La primera pasada detectó 22 fallos de entorno. Los roles sintéticos de PostgreSQL requerían autenticación de prueba en el contenedor efímero y el DSN de Macro API requería omitir la opción `gssencmode` que la propia API configura. Después de ajustar el entorno pasaron los casos afectados. PostgreSQL 17 con pgvector usó TLS en loopback.
- Frontend. 171 pruebas Vitest y 43 del Worker aprobadas. Lint, TypeScript y ambos builds correctos, con el aviso existente de tamaño del chunk de océano.
- Seguridad. Gitleaks, `git diff --check`, npm audit de producción y completo, y pip-audit público y local completados sin hallazgos.

La cobertura residual se detalla en el [contrato](fase-12-s4-contract.md). La verificación fue local con fixtures sintéticos e inspección de los cambios, con revisión independiente pendiente.
