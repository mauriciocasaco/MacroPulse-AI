# Despliegue público de MacroPulse AI

La fase 8.2 requiere autorización para los destinos de Cloudflare, Render,
Supabase y Google. La preparación local y las pruebas con datos sintéticos
se describen en el [informe de Fase 8.1](phase-8.1-public-ai-readiness.md).

## Preparar artefactos

Desde la raíz, ejecutar las pruebas de Python 3.12 con fixtures locales.
En `frontend`, usar la versión de Node indicada por `package.json` y ejecutar
`npm ci`, `npm run lint`, `npm run typecheck`, `npm test`,
`npm run test:worker`, `npm run build` y `npm run build:public`.
Comprobar el manifiesto y servir el build público desde la raíz del hostname HTTPS.

## Preparar Supabase DEMO

1. Crear un proyecto dedicado con PostgreSQL 17 o superior. Verificar organización,
   referencia y entorno en el panel. Comparar la referencia con DEV antes de
   cualquier conexión administrativa. No transferir bases, cuentas, roles o
   secretos de DEV.
2. Obtener la CA del proyecto. Aplicar [macro_projection.sql](../supabase/demo/macro_projection.sql)
   en la base `postgres` del proyecto DEMO con `psql`, `ON_ERROR_STOP` y
   `sslmode=verify-full`. El SQL crea el esquema técnico `marketpulse_demo`
   y el rol dedicado `marketpulse_demo_ro`. Usar `\password marketpulse_demo_ro`
   para configurar la contraseña aleatoria y luego habilitar `LOGIN` en el rol.
   Mantener el esquema fuera de los esquemas expuestos por PostgREST.
   [TLS de Supabase](https://supabase.com/docs/guides/platform/ssl-enforcement).
3. En la estación administrativa instalar `backend/requirements-demo-import.txt`.
   Capturar los cinco endpoints oficiales autorizados durante 8.2. Usar un
   directorio nuevo, privado y fuera de Git. La ventana debe incluir los tres
   eventos de Research. Revisar el origen, los derechos, el rango, los conteos,
   las unidades y las huellas SHA-256 antes de cargar.
4. Ejecutar la verificación offline sin DSN. Guardar la copia exacta del
   manifiesto y de las respuestas aprobadas junto con la evidencia de revisión.
   El manifiesto fija URL, fecha de captura y hash por serie. La huella identifica
   los bytes revisados y depende de la cadena de custodia de la captura HTTPS.

   ```powershell
   python -m backend.macro_data.demo_import capture data/demo/release --start 2018-01-01
   python -m backend.macro_data.demo_import import data/demo/release
   ```

5. En un proceso de administración separado, configurar
   `MARKETPULSE_DEMO_PROJECT_REF`, `MARKETPULSE_DEMO_SSL_ROOT_CERT` y
   `MARKETPULSE_DEMO_ADMIN_DSN` con la credencial de administración DEMO.
   La DSN sólo incluye `host`, `port`, `dbname`, `user` y `password`.
   Inyectarla con el gestor de secretos y retirarla al finalizar. Mantener
   vacío el entorno `PG*`. Aplicar en la referencia que se verificó en el panel.

   ```powershell
   python -m backend.macro_data.demo_import import data/demo/release --apply --project-ref <ref-demo-verificada>
   ```

   La carga usa un solo commit y rehúsa sustituir una captura más reciente.
   Cada serie queda vinculada a un conteo y un SHA-256 de sus filas ordenadas,
   su cobertura y su procedencia. El lector verifica esas huellas en la misma
   transacción de lectura. Repetir el mismo manifiesto conserva una proyección
   íntegra o repara sus filas si la verificación falla. La proyección sólo
   contiene filas sanitizadas y metadatos de procedencia. Los lectores activos pueden
   seguir usando su instantánea mientras se promueve una captura.
6. Verificar el rol real en una transacción read-only, las dos tablas permitidas,
   la ausencia de accesos adicionales y el rechazo de escrituras. Comprobar que
   el proyecto no contiene esquemas DEV, que el rol no es propietario ni miembro
   de otros roles y que los roles PostgREST no leen el esquema. Si Render
   necesita IPv4, verificar el pooler de sesión en puerto 5432 con el usuario
   `marketpulse_demo_ro.<ref-demo>`, TLS y el rol efectivo esperado.
   [Conexiones de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Preparar Render

1. Revisar [render.yaml](../render.yaml) y usar la rama y la cuenta aprobadas
   para `macropulse-public-demo`. Mantener un solo proceso y auto deploy
   deshabilitado. El chequeo de salud es TCP. El servicio exige autenticación
   Worker para todas las rutas HTTP.
   [Blueprint](https://render.com/docs/blueprint-spec) · [Health checks](https://render.com/docs/health-checks).
2. Configurar `MARKETPULSE_DEMO_PROJECT_REF`, `MARKETPULSE_DEMO_DSN` del rol
   read-only, y la CA como archivo secreto `macropulse-demo-ca.pem` en
   `/etc/secrets/macropulse-demo-ca.pem`. Conservar el nombre de entorno
   `MARKETPULSE_DEMO_SSL_ROOT_CERT`. El código impone `verify-full`.
   No configurar credenciales administrativas, valores DEV, `PG*` ni
   trazas de LangSmith. [Secret Files](https://render.com/docs/configure-environment-variables).
3. Generar un secreto de origen con 32 bytes aleatorios y guardarlo en ambos
   servicios como `MARKETPULSE_DEMO_ORIGIN_SECRET` en hexadecimal minúsculo.
   Confirmar 403 al consultar Render directamente sin el secreto.
4. Preparar una clave Gemini exclusiva del proyecto Google dedicado y limitarla
   a la API Generative Language. Guardarla como `MARKETPULSE_DEMO_GEMINI_API_KEY`
   sólo en Render. Elegir un modelo permitido en `MARKETPULSE_DEMO_GEMINI_MODEL`
   y verificar el contrato de tokens, cuenta y precio antes de activar
   `MARKETPULSE_DEMO_RESEARCH_ENABLED=true`. Conservar el perfil de análisis
   heredado desactivado.

## Preparar Cloudflare

1. Verificar la versión de Wrangler, los dos namespaces regionales de lecturas,
   la migración SQLite `v1` y el binding `RESEARCH_QUOTA` de
   [wrangler.jsonc](../frontend/wrangler.jsonc). Usar el servicio
   `macropulse-public-demo` y un único Durable Object con el nombre estable
   definido en el Worker. Conservar los activos compilados y el Worker
   delante de `/api` y `/api/*`.
2. Configurar `MARKETPULSE_DEMO_ORIGIN_URL` con el HTTPS de Render y
   `MARKETPULSE_DEMO_ORIGIN_SECRET` mediante el gestor de secretos. Configurar
   `MACROPULSE_RESEARCH_HOSTNAME` con el hostname HTTPS exacto de la PWA.
   Crear un widget Turnstile para ese hostname, configurar
   `MACROPULSE_TURNSTILE_SITE_KEY` y guardar `MACROPULSE_TURNSTILE_SECRET`
   como secreto server-side. Generar otro valor hexadecimal aleatorio de 32
   bytes para `MACROPULSE_RESEARCH_HASH_KEY`.
   [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/) ·
   [Siteverify](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).
3. Comprobar que Cloudflare suministra `CF-Connecting-IP`. Desactivar Pseudo
   IPv4 en modo de sustitución. Evitar Workers no confiables en la misma zona
   que puedan derivar la dirección de `x-real-ip` en subrequests. Revisar
   Logpush, logs de Worker y Render, APM y cabeceras. Excluir preguntas,
   tokens, credenciales e IP completas de registros persistentes.
   [Cabeceras Cloudflare](https://developers.cloudflare.com/fundamentals/reference/http-headers/).
4. Asociar el hostname público autorizado, activar Always Use HTTPS para
   redirigir tráfico HTTP y verificar el certificado y la redirección antes
   de compartir la PWA. Mantener `MACROPULSE_RESEARCH_ENABLED=false` hasta completar la verificación
   de gráficos, TLS, DNS, hostname de Turnstile y controles de facturación.
   Con la habilitación controlada, el catálogo anuncia la clave pública sólo
   si también está disponible el perfil Render y responde el Durable Object.

## Control de consumo y aceptación

- Las reservas usan UTC, con 3 por IP en 10 minutos, 10 al día por IP, 100 al
  día globales y 2 ejecuciones concurrentes. Cada admisión consume USD 0.10
  del presupuesto operativo de USD 10 por mes, incluidos los fallos. Es un
  estimado conservador de la aplicación. Los límites y facturación de Google
  se controlan por separado.
- Configurar alertas de Google Cloud Billing antes de USD 10 y en el objetivo
  máximo USD 15. Configurar las cuotas disponibles y revisar el consumo del
  proyecto y los límites del proveedor. Usar el tope de proyecto si está
  disponible y se verificó su comportamiento. Los reportes y la aplicación
  de los topes pueden tener retraso, por lo que el objetivo de USD 15 no
  constituye una garantía de facturación.
  [Precios Gemini](https://ai.google.dev/gemini-api/docs/pricing/) ·
  [Facturación Gemini](https://ai.google.dev/gemini-api/docs/billing).
- Verificar las cinco series, fuentes y unidades, rango de eventos, PWA
  instalable, protección de origen, rutas cerradas, Turnstile real, replay,
  límites, abstención y respuesta con referencias. Ensayar reinicio del
  Worker, cuotas y Render, arranque en frío y expiración de reservas. Usar
  solicitudes mínimas de Gemini y capturar únicamente evidencia sanitizada.
- Confirmar que el frontend sigue sirviendo gráficos con Research desactivado
  o saturado. Verificar la PWA en Safari móvil y un navegador de escritorio,
  incluida actualización del shell.

## Reversión

Para detener Research, publicar la configuración Worker con
`MACROPULSE_RESEARCH_ENABLED=false` y poner
`MARKETPULSE_DEMO_RESEARCH_ENABLED=false` en Render. Las solicitudes nuevas
se rechazan en el borde al activarse la configuración y las ya admitidas
pueden completar su plazo. Revocar la clave Gemini si se sospecha exposición.
Verificar que los gráficos siguen disponibles y conservar la identidad y
almacenamiento del Durable Object durante el rollback.

Si el problema está en la proyección, verificar el SHA-256 activo en
`marketpulse_demo.macro_catalog.bundle_sha256` y restaurar un manifiesto
anterior revisado con `import <bundle-anterior> --apply --project-ref <ref-demo-verificada>
--restore-from <sha256-activo>`. La comparación se hace dentro de la misma
transacción de escritura. Una actualización concurrente aborta la restauración.

Para retirar la demo completa, quitar la asociación del hostname Worker,
suspender Render y deshabilitar el rol DEMO. Rotar el secreto compartido de
forma coordinada si se sospecha exposición. Operar únicamente sobre DEMO.
