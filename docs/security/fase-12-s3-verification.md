# Fase 12.S3 — Verificación

Base `origin/dev` en `b6e6abde261ae14d29085aae6dd63e9003e8f6a2`. Gate 0 quedó registrado en `1c4c6e9`. La PR #34 pertenece a la base y la PR #43 cerró sin merge.

| Worktree | Commits en la rama de trabajo | Commits integrados por cherry-pick |
| --- | --- | --- |
| A · `fix/fase-12-s3-sensitive-input` | `6578dd3`, `0662ffe`, `aeac1db` | `979d923`, `e47f2c2`, `d4112b3` |
| B · `fix/fase-12-s3-egress` | `f6722b8`, `3612cd6`, `cbb650f` | `f6e70cb`, `36b80dd`, `9a5e852` |
| D · `fix/fase-12-s3-audit` | `cdfed17`, `8369f87`, `7be1ea3`, `fca1e32` | `eef3e91`, `8e4e3d8`, `d8a5cef`, `aa8802e` |

La integración mantuvo la propiedad exclusiva de archivos. A también cubrió la ruta HTTP local v2 y el firmante sintético compartido. B ajustó los fixtures de reintentos a una clave de formato realista. D cubrió el validador del Worker y el componente que muestra el error local. El cambio local en `frontend/wrangler.jsonc` permanece en el checkout original.

## Evidencia funcional

- Los casos sintéticos demuestran el 422 fijo y `no-store` con cero entrada al runner, cero consultas de la petición y cero solicitudes a Gemini. El origen mantiene verificación HMAC, consumo único y la marca de finalización. Se probó el replay de un rechazo y la liberación del lease en el Worker. El Worker sigue bloqueando su credencial administrativa literal antes del despacho.
- Los mocks capturaron el cuerpo completo de `countTokens` y `generateContent` para interpretación, RAG y Pure. Se verificaron las proyecciones, el texto original de preguntas admitidas, las citas de servidor, el resultado conceptual libre de Pure, el failover Flash → Flash-Lite y los reintentos acotados de Flash-Lite primario.
- Las pruebas adversariales verificaron el rechazo de PEM multilínea y formatos Unicode, la supresión de detalles y cabeceras de origen y la ausencia de preguntas, claves, cuerpos y argumentos de excepciones en los logs capturados.
- workerd ejecutó el Worker y el Durable Object con SQLite local. La prueba conectó FastAPI mediante un transporte sintético. Las pruebas SQL usaron PostgreSQL 17 con TLS y pgvector en un contenedor desechable de loopback.

## Resultados locales

| Estado | Verificación | Resultado |
| --- | --- | --- |
| PASS | Pytest dirigido | 271 aprobadas. La ejecución dirigida dejó 8 pruebas SQL para la suite completa |
| PASS | Suite backend y `compileall` | 1115 aprobadas y 1 omitida, incluidos PostgreSQL 17 con TLS y workerd |
| PASS | Cloudflare Worker | 42 aprobadas |
| PASS | Vitest | 167 aprobadas |
| PASS | Lint, TypeScript y builds local/pública | Ambos builds emitieron la advertencia de tamaño del chunk de océano |
| PASS | `pip-audit` público y local | Sin vulnerabilidades conocidas en los grafos auditados |
| PASS | `npm audit` producción y completo | Cero avisos |
| PASS | Gitleaks y `git diff --check` | Sin coincidencias en el rango de la fase ni errores de whitespace |
| FAIL | Resultado final local | Cero verificaciones fallidas |
| SKIP | Modelo opcional de M7C | `M7C_TEST_MODEL_CACHE` sin configurar |
| SKIP | Render, Gemini y Cloudflare desplegados | El alcance de las pruebas fue local y sintético |

La primera suite completa detectó una importación anticipada con Research deshabilitado. La corrección se verificó con el test específico y con la suite completa. La suite definitiva utilizó PostgreSQL 17 con TLS para cubrir también los casos que requerían ese entorno. Los estados de CI se consultan en la PR de integración.

## Límites

El detector prioriza formatos reconocibles. Una dirección postal, un nombre, prosa privada, un formato regional ambiguo o una ofuscación más compleja puede pasar. Si la composición bloquea una afirmación o el proveedor falla, el servidor conserva su selección determinista de afirmaciones verificadas y sus citas.
