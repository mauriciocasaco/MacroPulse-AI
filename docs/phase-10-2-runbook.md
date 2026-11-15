# Fase 10.2 — Operación del Worker público

## Tráfico y límites

El Worker valida método, ruta, origen y tamaño. Research público pasa por el
limitador de intentos, valida el cuerpo, verifica Turnstile y reserva una plaza
en el Durable Object antes de enviar la petición a Render. La IP se toma de
`CF-Connecting-IP`. Los headers de reenvío del visitante no se usan como
identidad. Conservar el ingreso directo por Cloudflare y volver a evaluar esa
frontera si se agregan Workers que hagan subrequests al mismo sitio.

| Control | Límite |
| --- | --- |
| Intentos Research por IP y vía | 30/minuto por ubicación de Cloudflare, aproximado |
| Research público por visitante | 20/10 min y 50/día UTC |
| Research público global | 2 reservas activas, 300/día UTC y 1.000/mes UTC |
| Autenticación administrativa | Bloqueo por IP tras 5 fallos en la ventana de 60 s |
| Research administrativo global | 1 reserva activa, 20/10 min, 50/día UTC y 300/mes UTC |
| GET catálogo e históricos | 30/minuto/IP y 20/minuto/IP, respectivamente |

La vía administrativa autentica antes de reservar. La verificación de
credenciales no inicia una investigación. Las reservas viven 90 s y cuentan
aunque falle el origen. Si la finalización es dudosa, la plaza se conserva
hasta expirar. FastAPI valida el secreto de origen y los plazos, y aplica su
propio límite de dos investigaciones en curso. Un administrador puede
competir por esos recursos aunque el presupuesto esté aislado. Las reservas
limitan investigaciones, con hasta dos generaciones Gemini por investigación,
y no representan un límite de facturación del proveedor. Consultá el
[presupuesto temporal de Research](public-deployment-runbook.md#presupuesto-temporal-de-research).

Los abusos limitados reciben 429 con `Retry-After`, un desafío rechazado
recibe 403, y un fallo de infraestructura o falta de capacidad recibe 503.
El cliente distingue un bloqueo de autenticación del presupuesto agotado.
[Semántica del limitador de Cloudflare](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).

## Caché

Solo se admiten los GET públicos de catálogo (60 s) e históricos oficiales
(300 s). La clave incluye host, endpoint, partición de la configuración del
origen y, en históricos, indicador y fechas canónicas. FastAPI autoriza la
escritura con `X-MacroPulse-Public-Cache: official-v1`. El Worker excluye errores,
credenciales, cookies, respuestas privadas o con `Vary`, y cuerpos mayores
de 3 MiB. Research y otros POST quedan excluidos. Las respuestas al navegador
conservan `no-store`. No se altera el contenido ni el `updated_at` de origen.

El HEAD autenticado de catálogo recalcula la disponibilidad de Research sin
conectar a PostgreSQL. Si el backend anterior no admite HEAD o retira el
opt-in, el Worker consulta un GET fresco. Un fallo de Render sigue afectando
al catálogo y a históricos sin hit vigente. Un hit vigente de históricos evita
Render. La prueba local contaba un GET de origen para dos lecturas idénticas
de históricos, y un GET más un HEAD para dos lecturas de catálogo. El ahorro
del catálogo es de trabajo en origen y no de peticiones HTTP. El caché depende
de la repetición por ubicación y no se sirve vencido durante caídas.
[Cache API de Cloudflare](https://developers.cloudflare.com/workers/runtime-apis/cache/).

## Verificación de la fase

- Worker 33/33, Vitest 163/163, lint, TypeScript y builds de ambas PWA.
- Validación anterior con workerd y FastAPI sintético, incluido el límite
  nativo, concurrencia administrativa en SQLite y el conteo de llamadas anterior.
- Los ocho casos de integración de `test_public_online.py` pasaron con
  PostgreSQL 17, pgvector y TLS en loopback. El primer intento con PostgreSQL 18
  sin esos requisitos no pudo preparar el fixture. Consultar el PR para el
  resultado agregado del backend. Las bases y los certificados fueron desechables.
- Backend con el entorno de CI y sin DSN, 887 aprobados y 141 omitidos.
- npm audit de producción y árbol completo, 0 vulnerabilidades. pip-audit
  no informó vulnerabilidades en los dos grafos de CI ni en los pins
  directos de M7.C. Consultar el PR para los siete checks del SHA final.
- Revisión interna de implementación. Los cambios deben pasar Gitleaks y
  CodeQL antes de su aprobación.

Las alertas Dependabot #1 y #2 corresponden a pytest <9.0.3
([GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g)).
Ambas rutas ya resuelven 9.0.3 en la base. Afectan a los tests en sistemas
Unix con posibilidad de interferencia local en directorios temporales, fuera
del runtime público. La alerta crítica #3
([GHSA-jhr6-gm9c-rqjv](https://github.com/advisories/GHSA-jhr6-gm9c-rqjv))
afecta al cargador de modelos local de M7.C si incorpora código Python desde
un modelo manipulado. El módulo es opcional y queda fuera de la imagen pública.
Se sube el pin de 5.2.0 a 5.6.0, la primera versión corregida. El estado de
las alertas de la rama por defecto depende de que GitHub reevalúe sus manifiestos.

## Despliegue manual y rollback

1. Registrar los SHA/versiones previos de Render y Cloudflare. Tras aprobación
   de publicación y checks, desplegar primero el SHA revisado en Render por
   **Manual Deploy**. Mantener la etiqueta de migración de Worker existente.
   Esta fase no requiere cambios de esquema ni de credenciales.
2. Mantener el Worker y bindings existentes `ASSETS`, `RESEARCH_QUOTA`,
   `CATALOG_RATE_LIMIT` y `HISTORY_RATE_LIMIT`. Comprobar el nuevo
   `RESEARCH_ATTEMPT_RATE_LIMIT` en [wrangler.jsonc](../frontend/wrangler.jsonc).
   Conservar `MARKETPULSE_DEMO_MACRO_ENABLED`, `MARKETPULSE_DEMO_ORIGIN_URL`,
   `MACROPULSE_RESEARCH_ENABLED`, `MACROPULSE_RESEARCH_HOSTNAME` y
   `MACROPULSE_TURNSTILE_SITE_KEY`. Mantener `MARKETPULSE_DEMO_ORIGIN_SECRET`,
   `MACROPULSE_RESEARCH_HASH_KEY`, `MACROPULSE_TURNSTILE_SECRET` y el opcional
   `MACROPULSE_ADMIN_SECRET` en su gestor. Render conserva su configuración de
   conexión de solo lectura y, si Research está habilitado, sus variables
   del proveedor. Consultar [el runbook de publicación](public-deployment-runbook.md).
   Tras autorización, desde `frontend` usar
   `npx --yes wrangler@4.162.0 deploy --config wrangler.jsonc`.
3. Con una prueba normal autorizada, verificar catálogo y gráficos en móvil
   y escritorio, un Research con Turnstile y una comprobación de credencial
   administrativa. Validar rechazos ante acceso directo a Render y confirmar
   que los headers internos no se exponen. Observar errores 429/503,
   consultas de históricos y uso del proveedor. Repetir una lectura de un
   histórico con la misma URL sin generar una ráfaga.
4. Si el problema es el caché, publicar la configuración
   `MACROPULSE_PUBLIC_CACHE_ENABLED=false` manteniendo los límites. Si se
   debe revertir el Worker completo, deshabilitar antes la credencial
   administrativa mediante el procedimiento vigente si la versión de
   destino no tiene presupuesto. Restaurar primero el Worker y, si hace
   falta, el SHA anterior de Render. Conservar el almacenamiento de cuotas
   y comprobar de nuevo catálogo, históricos y Research después del rollback.
