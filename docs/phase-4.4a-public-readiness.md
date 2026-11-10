# Fase 4.4A · Preparación de la demo pública

```text
Navegador/PWA HTTPS
        ├── assets estáticos ── Cloudflare Workers Static Assets
        └── /api ── Worker ── HTTPS + secreto ── FastAPI en Render
                                                        └── TLS + rol SELECT ── Supabase DEMO
```

Esta fase prepara código y archivos de configuración locales. La superficie
prevista ofrece catálogo y gráficas de tres índices sintéticos. La ruta de
análisis está deshabilitada. La [política de datos](public-data-policy.md)
describe qué materiales pueden usarse y el
[runbook](public-deployment-runbook.md) cubre los pasos de Fase 4.4B.

## Perfiles y controles

| Perfil | Contrato |
| --- | --- |
| `local` | Predeterminado. Conserva FastAPI, el proxy de Vite, el flujo de credenciales local, RAG y el proveedor simulado de la Fase 4.3. |
| `test` | Conserva las rutas y exige inyectar repositorios o configuración de prueba. No resuelve credenciales locales ni abre PostgreSQL por defecto. Las suites de integración usan PostgreSQL desechable en loopback con autorización explícita. |
| `public_demo` | Selecciona una aplicación dedicada sin debug, documentación OpenAPI, salud HTTP, escritura, comparador, latest, administración ni Gemini. El arranque verifica la configuración, el TLS con la CA del proyecto y los permisos del rol antes de admitir solicitudes. |

El proxy acepta GET para `/api/assets` y
`/api/assets/{1,2,3}/history`. Exige fechas de calendario con
`start` inclusiva y `end_exclusive` exclusiva. Envía sólo esos parámetros
y cabeceras creadas por el Worker al único origen Render configurado. Exige
HTTPS, no sigue redirecciones y no reenvía cookies, autenticación del visitante
ni errores internos. Las respuestas API tienen `Cache-Control: no-store`.
El service worker de la PWA sólo conserva su shell estático y excluye
`/api`. El build público identifica las series sintéticas y comunica
indisponibilidad, arranque en frío, timeout y límites.

FastAPI comprueba el secreto con comparación en tiempo constante antes de
usar la IP suministrada por el Worker. Rechaza acceso directo sin secreto válido
y no habilita CORS como control de acceso. El secreto es compartido, no está
ligado a un certificado de cliente y protege el origen mientras permanezca
privado. El rol DEMO se valida en el arranque y antes de cada lectura.
Debe carecer de membresías, privilegios de escritura y capacidad de ejecutar
funciones de usuario con privilegios elevados. Cada transacción es de sólo
lectura. El rol, la base, el host y el identificador de proyecto admitidos se
validan por separado del flujo DEV. Los resultados contienen únicamente datos
del esquema sintético dedicado.

## Uso y recursos

| Operación | Control |
| --- | --- |
| Catálogo | 30/min/IP en Cloudflare y por proceso en Render. |
| Históricos | 20/min/IP en ambas capas. Ventana obligatoria de hasta 2600 días, máximo 2600 barras y series fijas. |
| Análisis | 503 en el Worker, sin reenviar la solicitud. El origen autenticado valida JSON de hasta 8 KiB y pregunta de hasta 500 caracteres, y devuelve 503 para solicitudes válidas. El interruptor rechaza la activación mientras falte una cuota global. |
| Gemini y administración | Cero llamadas desde la demo y rutas administrativas ausentes. |

429 incluye `Retry-After`. Cloudflare aplica límites aproximados por ubicación.
El origen usa ventanas fijas en memoria en un proceso, con un máximo de 4096
claves derivadas por operación y ventana. Los reinicios borran los contadores,
las fronteras de ventana permiten ráfagas y NAT puede agrupar visitantes.
No constituyen una cuota global, deslizante o persistente. La aplicación
evita persistir o registrar IP completas y deshabilita el log de accesos
de Uvicorn. La configuración de observabilidad del proveedor debe revisarse
por separado. La concurrencia del origen se limita a cuatro operaciones y
responde 503 si está ocupado. La conexión PostgreSQL tiene un máximo de
cinco segundos, cada sentencia tres y cada espera de bloqueo uno. El Worker
limita la espera a 20 segundos y la respuesta a 3 MiB.

Turnstile puede añadir verificación frente a bots para un flujo interactivo.
No establece una cuota global por IP. Un Durable Object con estado coordinado
podría hacer atómicas las cuotas de 5 análisis por 10 minutos y 20 por día,
incluidos reinicios y concurrencia. Antes de habilitarlo hay que verificar
ventanas, política de identidad/retención y comportamiento ante fallas.
Esta fase no incorpora Turnstile ni Durable Objects.
[Rate limiting de Cloudflare](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/) ·
[Turnstile](https://developers.cloudflare.com/turnstile/) ·
[Durable Objects](https://developers.cloudflare.com/durable-objects/).

El perfil de Render instala un subconjunto de dependencias sin yfinance,
PyTorch ni Sentence Transformers. La referencia del plan gratuito es 512 MB
de memoria y 0.1 CPU. La descarga de pesos de `intfloat/multilingual-e5-small`
en precisión completa ronda los 471 MB. El tamaño del archivo no mide el RSS.
Con pesos, runtime, activaciones y servicio HTTP, la capacidad para inferencia
es incierta sin medir memoria y latencia bajo carga. No se midió RSS de E5
en esta fase. [Render](https://render.com/articles/from-side-project-to-production-scaling-your-first-app) ·
[Archivos del modelo E5](https://huggingface.co/intfloat/multilingual-e5-small/tree/main).

Para el análisis local, `MARKET_ANALYSIS_EMBEDDING_CACHE` configura E5 con un
cache local preparado. Al omitirla, la recuperación usa coincidencias léxicas
de términos y asociaciones SQL y explicita la falta de puntuación semántica.
La política de consultas retrospectivas puede excluir puntuación vectorial
aunque el cache exista. Los dos métodos pueden recuperar evidencia diferente.
Un despliegue futuro debería evaluar recuperación, atribución, cobertura
y recursos sobre un corpus autorizado antes de optar por una estrategia.

## Variables públicas

- Render, requeridas por el perfil público: `MARKETPULSE_MODE`,
  `MARKETPULSE_DEMO_PROJECT_REF`, `MARKETPULSE_DEMO_DSN`,
  `MARKETPULSE_DEMO_SSL_ROOT_CERT`, `MARKETPULSE_DEMO_ORIGIN_SECRET`.
- Render, límites y disponibilidad: `MARKETPULSE_DEMO_ANALYSIS_ENABLED`,
  `MARKETPULSE_DEMO_CATALOG_PER_MINUTE`, `MARKETPULSE_DEMO_HISTORY_PER_MINUTE`,
  `MARKETPULSE_DEMO_CONCURRENCY`.
- Worker: `MARKETPULSE_DEMO_ORIGIN_URL`, `MARKETPULSE_DEMO_ORIGIN_SECRET`.
  Los bindings son `ASSETS`, `CATALOG_RATE_LIMIT` y `HISTORY_RATE_LIMIT`.

## Verificación local

- 37 pruebas del modo público con TestClient y conexiones sintéticas. Cubren
  arranque, autenticación, rutas, datos, límites, errores y cero análisis.
- La ejecución sin DSN de integración aprobó 302 pruebas Python y omitió
  71 que requieren PostgreSQL desechable. La suite de integración se
  ejecuta por separado contra PostgreSQL local con fixtures sintéticos.
- SQL, rol y conector probados en PostgreSQL 17 desechable sobre TLS con CA
  de prueba. Se verificaron 6576 filas sintéticas, tres índices, 2192 barras
  en una historia completa, denegación de escrituras y fallo cerrado ante
  privilegios adicionales. El test dirigió el transporte a loopback.
- Frontend verificado con lint, 51 pruebas Vitest, cuatro pruebas de proxy
  con Node y builds local y público. Las pruebas del Worker usan bindings
  simulados y no emulan la distribución regional de Cloudflare.
- El entorno de los proveedores, el comportamiento de arranque en frío y
  la integración HTTPS desplegada quedan pendientes de Fase 4.4B.

## Pendientes para Fase 4.4B

- [ ] Aprobar el dataset exacto y confirmar la separación de DEMO de DEV.
- [ ] Configurar Supabase DEMO y probar TLS, permisos efectivos y ausencia de
      exposición por las APIs administradas.
- [ ] Crear Render y el Worker de forma autorizada y validar límites, dominios,
      logs, arranque en frío y caché en el entorno desplegado.
- [ ] Validar rotación del secreto y respuesta al uso abusivo o saturación del
      origen. El hostname Render sigue expuesto a tráfico de red sin autenticar.
- [ ] Mantener el análisis deshabilitado mientras falten corpus autorizado,
      cuotas globales, evaluación de recuperación y verificación de recursos.
