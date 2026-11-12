# Despliegue público de MacroPulse AI

La fase 8.2 usa el proyecto Supabase `swpithkjviavycrxpwed` con la proyección
`marketpulse_demo` y el rol de lectura `marketpulse_demo_ro`. Ejecutar este
procedimiento tras aprobar los destinos y los cambios de cada proveedor.
Identificar al operador, el SHA aprobado con A, B y C, la ventana y los puntos
de reversión. Registrar fecha UTC, resultados y huellas en un registro privado
sin credenciales, consultas de usuarios ni datos DEV.

La [preparación de Fase 8.1](phase-8.1-public-ai-readiness.md) documenta los
controles de aplicación. El [plan de Fase 8.2](phase-8.2-shared-supabase-plan.md)
define las condiciones de integración. En el SHA de Fase 8.1 el SQL, el
importador y el backend todavía presuponen un proyecto dedicado.

## Preparación local

Desde el checkout integrado, ejecutar con Python 3.12 las pruebas de proyección,
importador y API pública con fixtures sintéticos. Para las pruebas SQL usar
PostgreSQL 17+ desechable en loopback, con TLS para los casos que lo requieran.
En `frontend`, usar la versión de Node admitida por `package.json` y ejecutar
`npm ci`, `npm run lint`, `npm run typecheck`, `npm test`,
`npm run test:worker`, `npm run build` y `npm run build:public`.
Revisar el manifiesto PWA, los activos del build público y la configuración de
Render y Wrangler del mismo SHA. Usar dependencias locales o en caché para una
validación sin red.

## 8.2A — Supabase compartido

1. En el panel de Supabase, verificar la cuenta, la organización, el nombre y
   la referencia exacta `swpithkjviavycrxpwed`. Confirmar la base `postgres`,
   PostgreSQL 17+ y los hostnames de conexión en **Connect**. Descargar la CA
   indicada por Supabase y verificar la cadena y el hostname del endpoint
   elegido con `sslmode=verify-full`. Para administración, preferir el endpoint
   directo `db.swpithkjviavycrxpwed.supabase.co:5432` cuando sea accesible.
   [TLS de Supabase](https://supabase.com/docs/guides/platform/ssl-enforcement) ·
   [Conexiones de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).
2. Confirmar un backup previo del proyecto o punto de recuperación aprobado.
   Registrar identificador, fecha UTC, cobertura y procedimiento de restauración
   ensayado en un entorno aislado. Si ya existe DEMO, conservar además su bundle
   verificado y su huella activa. El rollback operativo de DEMO usa la
   proyección y el rol dedicados. Una restauración del proyecto compartido
   requiere un incidente y autorización propios por su impacto en DEV.
3. Desde una sesión administrativa aislada, inventariar en modo de solo lectura
   los esquemas, propietarios, roles, membresías, ACL de base/esquema/objetos,
   privilegios por defecto y esquemas expuestos por la API de datos. Incluir los
   grants a `PUBLIC`, las funciones ejecutables y cualquier objeto previo en
   `marketpulse_demo`. En `psql -X` usar `\dn+`, `\du+`, `\l+ postgres`,
   `\dp`, `\ddp` y `\df+` para orientar la inspección. Guardar una referencia
   privada del inventario y de los accesos DEV existentes. Detenerse ante un
   esquema o rol DEMO preexistente cuyo propietario, ACL u origen se desconozca.
4. Revisar la huella y el diff de la migración entregada por A. Ensayarla en
   PostgreSQL desechable con esquemas DEV sintéticos, ACL y privilegios por
   defecto representativos. Comparar antes y después los permisos y objetos
   ajenos a DEMO. Revisar tanto los grants directos como los efectivos. Un
   privilegio concedido a `PUBLIC` también llega al rol DEMO aunque se revoque
   del rol individualmente. Si `TEMP`, `CREATE`, lecturas o funciones accesibles
   rompen el aislamiento o el contrato de B, detener el aprovisionamiento y
   resolverlo con A y B. Cualquier cambio que afecte DEV requiere autorización
   específica. El SQL dedicado de 8.1 incluye revocaciones globales de
   `PUBLIC` y queda excluido de este procedimiento.
5. Aplicar únicamente la migración compartida revisada de A en el destino
   verificado. En el proceso aislado de `psql`, establecer
   `PGSSLMODE=verify-full` y `PGSSLROOTCERT` con la ruta absoluta a la CA
   verificada. Pasar el host, la base y el usuario explícitamente y obtener
   la contraseña del gestor de secretos para el prompt. Por ejemplo, para
   el endpoint directo:

   ```powershell
   psql -X -v ON_ERROR_STOP=1 --host=db.swpithkjviavycrxpwed.supabase.co --port=5432 --dbname=postgres --username=postgres --password -f "<sql-aprobado-de-A>"
   ```

   La migración debe crear `marketpulse_demo`, sus dos tablas y
   `marketpulse_demo_ro` con `NOLOGIN` inicial. Mantener el esquema fuera de
   los esquemas expuestos por PostgREST. Verificar el resultado de la
   transacción antes de cargar.
6. En una estación autorizada con `backend/requirements-demo-import.txt`,
   capturar únicamente los endpoints oficiales aprobados. Usar un directorio
   nuevo y privado fuera de Git. Validar sin DSN el manifiesto, los bytes
   capturados, las huellas SHA-256, los derechos de redistribución, unidades,
   multiplicadores, fechas de captura y conteos. La ventana debe cubrir COVID
   (2020-02-01 a 2020-12-31), Ucrania (2022-02-01 a 2024-02-29) e IA generativa
   (2023-09-01 a 2026-09-30) y conservar la cobertura de la tasa de depósito.
   El SHA del bundle identifica la captura revisada. Conservar la evidencia
   de su origen HTTPS.

   ```powershell
   python -m backend.macro_data.demo_import capture "<bundle-privado>" --start 2018-01-01
   python -m backend.macro_data.demo_import import "<bundle-privado>"
   ```

   | ID público | Serie oficial |
   | --- | --- |
   | `eur-usd` | ECB `EXR.D.USD.EUR.SP00.A` |
   | `m3-stock` | ECB `BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E` |
   | `deposit-rate` | ECB `FM.B.U2.EUR.4F.KR.DFR.LEV` |
   | `real-gdp-qoq` | Eurostat `namq_10_gdp`, `Q / CLV_PCH_PRE / SCA / B1GQ / EA21` |
   | `hicp-yoy` | Eurostat `prc_hicp_minr`, `M / RCH_A / TOTAL / EA` |
7. Cargar con el importador compartido aprobado por A y B. El contrato
   conserva la validación offline y la confirmación explícita del proyecto
   para escritura. Usar el mecanismo de opt-in administrativo que confirmen
   A y B. En el proceso administrativo aislado, proporcionar
   `MARKETPULSE_DEMO_PROJECT_REF`, `MARKETPULSE_DEMO_SSL_ROOT_CERT` y
   `MARKETPULSE_DEMO_ADMIN_DSN` mediante el gestor de secretos. Mantener vacío
   el entorno `PG*` del importador. La DSN contiene sólo `host`, `port`,
   `dbname`, `user` y `password`. El contrato de comando a confirmar con A y B es:

   ```powershell
   python -m backend.macro_data.demo_import import "<bundle-privado>" --apply --project-ref swpithkjviavycrxpwed
   ```

   Comprobar que la transacción sólo modifica las dos tablas DEMO, que las
   cinco filas de catálogo comparten el SHA-256 aprobado en `bundle_sha256`
   y que conteos y huellas por serie coinciden con las observaciones. Archivar
   el manifiesto, la evidencia y el bundle aprobado. Retirar la DSN
   administrativa del proceso al terminar.
8. Configurar la contraseña aleatoria exclusiva con `\password marketpulse_demo_ro`
   en la sesión administrativa y habilitar `LOGIN` según el procedimiento de
   A. Abrir una conexión nueva como `marketpulse_demo_ro` mediante la misma
   ruta que usará Render. Usar `\conninfo` y ejecutar:

   ```sql
   SHOW default_transaction_read_only;
   BEGIN READ ONLY;
   SELECT current_database(), current_user, session_user,
          current_setting('transaction_read_only');
   SELECT n.nspname, c.relname
   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
     AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
     AND has_schema_privilege(n.oid, 'USAGE')
     AND has_any_column_privilege(c.oid, 'SELECT')
   ORDER BY 1, 2;
   SELECT indicator_id, series_key, captured_at, projected_rows, bundle_sha256
   FROM marketpulse_demo.macro_catalog ORDER BY indicator_id;
   ROLLBACK;
   ```

   Esperar la base `postgres`, ambos usuarios iguales al rol DEMO, lectura
   activa por defecto y exactamente las dos relaciones DEMO. Desde la sesión
   administrativa, comparar con el inventario inicial. Comprobar ausencia de
   membresías, propiedad de objetos, atributos elevados, privilegios efectivos
   de escritura o delegación y acceso a datos, secuencias o funciones ajenas
   a DEMO. Comprobar `USAGE` y `SELECT` efectivos de `anon`,
   `authenticated` y `service_role` sobre DEMO. Deben quedar denegados.
   Confirmar que los roles y accesos DEV conservan el resultado del preflight.
   Usar las pruebas negativas de A/B con fixtures para operaciones de escritura.
   Durante el despliegue, abrir una nueva transacción con `BEGIN READ ONLY`,
   ejecutar `DELETE FROM marketpulse_demo.macro_observations WHERE FALSE` y
   esperar un error de privilegios o de transacción de solo lectura. Ejecutar
   `ROLLBACK` tras la prueba. Mantener el origen apagado y volver el rol a
   `NOLOGIN` si falla alguna comprobación.

## 8.2B — Render

1. Crear el servicio `macropulse-public-demo` en la cuenta aprobada con
   [render.yaml](../render.yaml) del SHA integrado. Confirmar el plan en el
   panel, auto deploy deshabilitado, un proceso Uvicorn y chequeo TCP. Configurar
   `MARKETPULSE_MODE=public_demo` y `MARKETPULSE_DEMO_PROJECT_REF=swpithkjviavycrxpwed`.
   Usar `MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT=true` sólo tras superar 8.2A y
   verificar que B rechaza el destino sin ese opt-in y restringe el acceso al
   rol DEMO y al proyecto esperado. Mantener `MARKETPULSE_DEMO_ANALYSIS_ENABLED=false`
   y `MARKETPULSE_DEMO_RESEARCH_ENABLED=false`. Agregar la clave Gemini recién en 8.2D.
   [Blueprint](https://render.com/docs/blueprint-spec) ·
   [Health checks](https://render.com/docs/health-checks).
2. Inyectar `MARKETPULSE_DEMO_DSN` como secreto con la credencial DEMO de
   lectura. Usar `dbname=postgres` y `port=5432`. En conexión directa, usar
   el hostname del proyecto y `user=marketpulse_demo_ro`. Si Render requiere
   IPv4, tomar el hostname exacto del pooler de sesión de **Connect** y usar
   `user=marketpulse_demo_ro.swpithkjviavycrxpwed`; el rol efectivo sigue
   siendo `marketpulse_demo_ro`. Usar modo sesión en lugar del endpoint
   transaccional en puerto 6543. Subir la CA verificada para el endpoint usado
   como archivo secreto `macropulse-demo-ca.pem`. El backend impone
   `verify-full` con `MARKETPULSE_DEMO_SSL_ROOT_CERT=/etc/secrets/macropulse-demo-ca.pem`.
   Mantener las credenciales DEV y administrativas fuera de Render. Evitar
   `PG*`, grupos de variables compartidos y trazas de LangSmith.
   [Secret Files](https://render.com/docs/configure-environment-variables).
3. Generar un secreto de origen de 32 bytes aleatorios y configurar el mismo
   valor de 64 dígitos hexadecimales minúsculos en Render y Worker como
   `MARKETPULSE_DEMO_ORIGIN_SECRET`. Guardarlo mediante el gestor de secretos.
   Verificar TLS y rol efectivo con la misma ruta y CA que usa Render. En el
   arranque, exigir que B valide alcance, solo lectura e integridad de la
   proyección. Confirmar el estado de arranque con evidencia sanitizada.
4. Desde un cliente de prueba que tome el secreto del gestor, consultar
   `GET /macro/indicators` en el hostname HTTPS asignado por Render con
   `X-MarketPulse-Origin` y `X-MarketPulse-Client-IP` con una IP válida de
   prueba. Esperar 200, las cinco series y ausencia de
   `X-MacroPulse-Public-Research: true`. Repetir sin el secreto y con uno
   incorrecto para verificar 403. Mantener el secreto fuera de líneas de
   comandos, historial, capturas y logs. Un chequeo TCP exitoso requiere
   además estas comprobaciones HTTP para aceptar el servicio.

## 8.2C — Cloudflare

1. Configurar el Worker `macropulse-public-demo` del SHA aprobado con
   [wrangler.jsonc](../frontend/wrangler.jsonc). Conservar `workers.dev` y
   previews apagados, activos públicos con binding `ASSETS` y precedencia
   del Worker para `/api` y `/api/*`. Verificar los límites de borde
   `CATALOG_RATE_LIMIT` y `HISTORY_RATE_LIMIT` (30 y 20 solicitudes por
   60 segundos, respectivamente). Conservar la migración SQLite `v1`,
   el binding `RESEARCH_QUOTA` a `ResearchQuota` y la instancia estable
   `macropulse-research-global-v1`.
2. Fijar `MARKETPULSE_DEMO_ORIGIN_URL` a la raíz HTTPS asignada por Render
   bajo `.onrender.com`, con el secreto de origen de 8.2B como secreto Worker.
   Conservar `MARKETPULSE_DEMO_MACRO_ENABLED=true` y
   `MACROPULSE_RESEARCH_ENABLED=false`. Configurar el hostname público
   autorizado, el certificado HTTPS y la redirección HTTP a HTTPS. Confirmar
   que Cloudflare suministra `CF-Connecting-IP`. Evitar Pseudo IPv4 en modo
   de sustitución y Workers no confiables en la misma zona que puedan alterar
   `x-real-ip` en subrequests.
   [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/) ·
   [Cabeceras Cloudflare](https://developers.cloudflare.com/fundamentals/reference/http-headers/).
3. Crear un widget Turnstile para el hostname público. Configurar
   `MACROPULSE_RESEARCH_HOSTNAME` con sólo ese hostname, sin protocolo ni
   puerto, y `MACROPULSE_TURNSTILE_SITE_KEY` con su clave pública. Guardar
   `MACROPULSE_TURNSTILE_SECRET` y `MACROPULSE_RESEARCH_HASH_KEY` como secretos
   del Worker. Generar la clave HMAC como 32 bytes aleatorios independientes
   del secreto de origen, en hexadecimal minúsculo. Verificar la configuración
   de hostname y la acción `research`. Completar la prueba Siteverify en 8.2D.
   [Siteverify](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).
4. En el hostname HTTPS, comprobar `GET /api/macro/indicators` y los cinco
   históricos en `/api/macro/indicators/<id>/history?start=<AAAA-MM-DD>&end=<AAAA-MM-DD>`.
   Comparar con el catálogo aprobado las fuentes, fechas de captura, unidades,
   conteos para la ventana y valores de muestra. Revisar los gráficos,
   las ventanas de eventos y el comportamiento ante un rango inválido.
   Confirmar 404 en rutas API ajenas al catálogo, los históricos y
   `/api/macro/analysis`. Un POST JSON válido del mismo origen a esta última
   ruta debe devolver 503 con Research apagado. Verificar que el catálogo
   carece de `X-MacroPulse-Turnstile-Sitekey`.
5. Abrir la PWA en Safari móvil y un navegador de escritorio. Verificar
   instalación, actualización del shell, gráficos y cambio de rango. Revisar
   Logpush, logs de Worker y Render, APM y cabeceras. Excluir preguntas,
   tokens, credenciales e IP completas de registros persistentes.

## 8.2D — Gemini y Research v2

1. Crear una clave exclusiva para DEMO en un proyecto Google dedicado y
   restringirla a la API Generative Language. Validar el modelo admitido por
   el SHA integrado, su disponibilidad, límites de tokens y tarifa vigente.
   Revisar las condiciones, las regiones y la retención aplicables a la
   audiencia. Configurar primero facturación, cuotas y alertas. Guardar
   `MARKETPULSE_DEMO_GEMINI_API_KEY` sólo en Render y, si corresponde, fijar
   allí el modelo aprobado en `MARKETPULSE_DEMO_GEMINI_MODEL`.
   [Precios Gemini](https://ai.google.dev/gemini-api/docs/pricing/) ·
   [Facturación Gemini](https://ai.google.dev/gemini-api/docs/billing) ·
   [Términos](https://ai.google.dev/gemini-api/terms).
2. Activar `MARKETPULSE_DEMO_RESEARCH_ENABLED=true` en Render con el borde
   todavía apagado. Comprobar con el cliente autenticado de 8.2B que el
   catálogo de origen anuncia `X-MacroPulse-Public-Research: true`. Confirmar
   que las solicitudes públicas siguen bloqueadas. Activar
   `MACROPULSE_RESEARCH_ENABLED=true` en el Worker en una ventana controlada.
   El catálogo público debe anunciar la clave Turnstile cuando estén listos
   el perfil de Render y el Durable Object.
3. Desde la PWA, resolver un desafío real y hacer el mínimo de consultas
   aprobadas. Por ejemplo, seleccionar COVID y EUR/USD y preguntar por su
   evolución durante la ventana. Verificar referencias, cifras y unidades
   contra la proyección. Preguntar por la causa de un movimiento o por una
   proyección fuera del alcance para comprobar abstención. Probar token ausente,
   inválido y reutilizado y comprobar su rechazo. Revisar que los fallos
   previos a la admisión no generen llamadas a Gemini. Conservar sólo
   resultados sanitizados.
4. Comprobar `503` por concurrencia y `429` por cuota o presupuesto, ambos
   con `Retry-After`. Verificar persistencia de reservas tras recrear la
   instancia y expiración de leases.
   Cubrir límites diarios y mensuales con fixtures locales, sin saturar el
   proveedor. Confirmar que un arranque en frío o la saturación de Research
   permiten recuperar la navegación y los gráficos. Ensayar el apagado de
   Research y comprobar el rechazo en el borde y el catálogo sin clave
   Turnstile. Dejarlo apagado hasta aprobar la activación operativa.

### Presupuesto y facturación

- El Durable Object reserva USD 0,10 estimados por admisión, incluidos
  fallos y timeouts. El presupuesto interno inicial es USD 10 por mes UTC
  (100 admisiones). Aplica además 3 por IP en 10 minutos, 10 al día por IP,
  100 al día globales y 2 ejecuciones concurrentes.
- Google factura el uso real según el modelo, los tokens y la tarifa. En el
  proyecto dedicado, configurar alertas antes de USD 10 y en el objetivo
  de USD 15. Configurar las cuotas disponibles y verificar en el panel
  si existe un tope aplicable al proyecto y cómo opera. Las alertas y la
  medición pueden demorarse. Revisar el consumo real durante la activación.
- Render puede sumar costo de cómputo o ancho de banda según plan y uso.
  Cloudflare puede sumar costo por Workers, Durable Objects, almacenamiento y
  otros servicios habilitados según plan y uso. Registrar las condiciones del
  panel de cada cuenta antes de activar.
  [Precios Render](https://render.com/pricing) ·
  [Precios Workers](https://developers.cloudflare.com/workers/platform/pricing/) ·
  [Precios Durable Objects](https://developers.cloudflare.com/durable-objects/platform/pricing/).
- Reutilizar Supabase evita un segundo proyecto. DEMO y DEV comparten recursos
  del proyecto, incluidos CPU, conexiones, almacenamiento e I/O. Registrar
  métricas iniciales y revisar las de DEV durante las pruebas de arranque y
  tráfico. Si se observa degradación, apagar la demo mientras se diagnostica.

## Reversión

| Acción | Procedimiento y comprobación |
| --- | --- |
| Desactivar Gemini | Apagar Research en el borde, quitar `MARKETPULSE_DEMO_GEMINI_API_KEY` de Render y aplicar la configuración. Revocar la clave dedicada en Google si se retira el acceso o hay exposición. Verificar ausencia de nuevas llamadas y catálogo e históricos disponibles. |
| Desactivar Research | Aplicar primero `MACROPULSE_RESEARCH_ENABLED=false` en Worker y después `MARKETPULSE_DEMO_RESEARCH_ENABLED=false` en Render. Las solicitudes ya admitidas pueden completar dentro de su plazo. Verificar 503 en el borde, catálogo sin clave Turnstile y gráficos disponibles. |
| Revertir Worker | Seleccionar una versión conocida compatible con los bindings y la configuración actual, con Research apagado. Mantener el hostname, el binding, la clase, el nombre y el almacenamiento del Durable Object. Si la versión anterior exponía Research, publicar el artefacto conocido con el interruptor apagado. Validar HTTPS, catálogo, históricos y rutas cerradas. |
| Revertir Render | Desplegar un SHA conocido que ya sea compatible con el destino compartido. Verificar manualmente variables, archivo CA, secreto y Research apagado. Si falta una versión compatible, suspender Render y retirar el hostname público mientras se recupera el servicio. Repetir las pruebas TLS y HTTP de 8.2B. |
| Restaurar la proyección DEMO | Leer el SHA activo único de las cinco filas de `marketpulse_demo.macro_catalog.bundle_sha256`. Validar offline el bundle anterior. Con el importador compartido aprobado, repetir 8.2A.7 con el bundle anterior y `--restore-from <sha256-activo>`. Exigir comparación bajo el bloqueo de escritura y abortar si cambió el SHA. Comprobar de nuevo conteos, huellas, grants y catálogos. Si falla la primera carga y falta un bundle previo, detener el origen y usar el rollback de A limitado a objetos DEMO aprobados. |
| Revocar credenciales DEMO | Retirar la ruta pública y suspender Render. Mediante el procedimiento administrativo aprobado por A, aplicar `ALTER ROLE marketpulse_demo_ro NOLOGIN PASSWORD NULL` tras comprobar el proyecto y la identidad del rol. Verificar o cerrar sólo sesiones de ese rol, incluidas las del pooler según corresponda, y confirmar que una conexión nueva falla. Retirar la DSN de Render y rotar el secreto de origen coordinadamente si corresponde. |

Para cualquier operación sobre PostgreSQL, volver a comprobar destino, CA,
objeto o rol exacto y alcance autorizado. Conservar evidencias de que DEV
mantiene sus objetos, accesos y servicio tras la reversión.
