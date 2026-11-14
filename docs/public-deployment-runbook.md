# Publicación de MacroPulse AI

M8.3 sirve la PWA en Cloudflare y tres rutas macroeconómicas en FastAPI.
El catálogo y las observaciones se leen en tiempo real desde vistas sobre
`macro_data` en el Supabase `swpithkjviavycrxpwed`. Las vistas filtran
campos internos y valores confidenciales. No se necesita cargar un bundle.
Los procedimientos históricos de M8.2 están documentados en [el contrato de la proyección](m8-shared-db-contract.md).

## Supabase

1. Con autorización de migración, aplicar [202611120001_macro_online.sql](../supabase/migrations/202611120001_macro_online.sql)
   como el propietario de las relaciones de origen en la base `postgres`,
   mediante TLS `verify-full`. La migración crea un rol con `NOLOGIN`,
   vistas y una tabla editorial en un esquema privado. Mantener
   `macropulse_online` fuera de los esquemas expuestos por PostgREST.
2. Provisionar una contraseña única de alta entropía mediante un canal seguro y
   habilitar `LOGIN` para `macropulse_online_ro`. Confirmar que el lector no
   tiene acceso a otras bases ni a datos privados por grants heredados de
   `PUBLIC`. El arranque de Render verifica los permisos efectivos en la base
   de destino, incluido el acceso a tablas, columnas y rutinas de `pg_net`.
   Si la comprobación falla, revisar los grants administrados antes de activar la ruta pública.
3. Verificar desde una sesión del lector que puede consultar las vistas y que
   se rechazan lecturas de las tablas fuente, cambios de rol y escrituras aun
   al pedir una transacción de lectura y escritura.

## Render

Usar [render.yaml](../render.yaml) desde la raíz del repositorio y desplegar
manualmente el SHA elegido. El plan Starter evita que la suspensión por
inactividad del plan gratuito exceda el plazo del Worker. Render comprueba el
arranque por TCP. Mantener un único worker Uvicorn y conservar deshabilitados
los logs de acceso, la depuración y las trazas de LangSmith. [Planes gratuitos](https://render.com/docs/free) · [Comprobaciones de Render](https://render.com/docs/health-checks)

- Configurar `MACROPULSE_ONLINE_DSN` con exactamente `host`, `port`, `dbname`,
  `user` y `password`. Usar puerto 5432 y base `postgres`. El host directo
  es `db.swpithkjviavycrxpwed.supabase.co` con usuario `macropulse_online_ro`.
  Si se usa el pooler de sesión, usar su hostname verificado y
  `macropulse_online_ro.swpithkjviavycrxpwed`. Subir el certificado
  correspondiente al hostname como archivo secreto `macropulse-online-ca.pem`.
- Configurar `MARKETPULSE_DEMO_ORIGIN_SECRET` con 32 bytes aleatorios
  representados por 64 caracteres hexadecimales minúsculos. Usar el mismo
  secreto en Cloudflare. Los nombres internos conservan compatibilidad con M8.2.
- Comprobar que la consulta directa al origen sin secreto devuelve 403. Con la
  credencial y una IP de prueba, comprobar los cinco indicadores, sus fechas de
  cobertura, la lectura de históricos y el rechazo de las rutas de activos.

## Cloudflare y Gemini

1. En `frontend`, ejecutar `npm ci` y `npm run build:public`. Desplegar con
   [wrangler.jsonc](../frontend/wrangler.jsonc) en el Worker existente para
   preservar el Durable Object de cuotas. Asociar un dominio personalizado
   HTTPS, activar la redirección HTTP a HTTPS y enlazarlo desde el portfolio. Configurar `MARKETPULSE_DEMO_ORIGIN_URL`
   con la raíz HTTPS de Render. Mantener deshabilitados `workers.dev` y las
   URLs de vista previa. [Dominio personalizado de Workers](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
2. Para activar Research, crear una clave Gemini dedicada al servicio y guardarla
   como `MARKETPULSE_DEMO_GEMINI_API_KEY` solo en Render. Configurar
   `MARKETPULSE_DEMO_RESEARCH_ENABLED=true`. El perfil usa `gemini-3.6-flash`
   y conserva hasta dos generaciones por investigación y el preflight de tokens.
   Research público admite 20 solicitudes por IP en 10 minutos móviles, 50 por IP
   al día UTC, 300 globales al día UTC y 1.000 globales al mes UTC, con hasta dos
   investigaciones simultáneas. Cada admisión cuenta aunque falle.
   [Precios de Gemini](https://ai.google.dev/gemini-api/docs/pricing)
3. Crear un widget Turnstile para ese dominio. Configurar
   `MACROPULSE_RESEARCH_HOSTNAME`, `MACROPULSE_TURNSTILE_SITE_KEY`,
   `MACROPULSE_TURNSTILE_SECRET` y `MACROPULSE_RESEARCH_HASH_KEY` en Cloudflare.
   El hash usa otros 32 bytes aleatorios en hexadecimal. Fijar
   `MACROPULSE_RESEARCH_ENABLED=true` en la configuración de Wrangler usada
   para publicar. Conservar los secretos en Render o Cloudflare.
4. Verificar la instalación PWA, el período `All`, un histórico anterior a
   siete años, una pregunta con fechas libres y una con cita documental.
   Para apagar Research, desactivarlo primero en el Worker y luego en Render.
   El catálogo y los gráficos siguen disponibles. Para retirar la publicación,
   quitar el dominio del Worker y suspender Render. Revocar el login del lector
   con `ALTER ROLE macropulse_online_ro NOLOGIN PASSWORD NULL` y cerrar sus
   sesiones activas. Para revertir una versión, desplegar un SHA compatible con
   las vistas y repetir las comprobaciones del catálogo y de Research.

## Acceso administrador de Research

La PWA verifica la credencial por HTTPS en el mismo Worker antes de habilitar
el modo administrador. El Worker vuelve a verificarla en cada investigación.
Cinco fallos desde la misma IP bloquean la verificación durante el resto de
la ventana de un minuto, contada desde el primer fallo. El modo administrador
comparte los límites de Render y Gemini y queda sujeto al interruptor global
de Research.

1. Generar **32 bytes aleatorios con un generador criptográfico**, en
   **64 caracteres hexadecimales en minúscula**, usando un gestor de contraseñas
   o un generador seguro local. Usar una clave exclusiva para este acceso.
   Pegar el valor directamente en el campo enmascarado de Wrangler. Mantenerlo
   fuera de archivos, argumentos, historial de shell y registros.
2. Desde el directorio de este checkout, desplegar el código en el Worker
   existente una vez revisado. El nombre, la clase SQLite y el nombre de
   instancia de cuotas públicas son los mismos, por lo que se preservan los
   contadores. El build público se ejecuta desde la configuración de Wrangler.

   ```powershell
   Set-Location frontend
   npm ci
   npm run test:worker
   npm test
   npx --yes wrangler@4.162.0 deploy --config wrangler.jsonc
   ```

3. Desde ese mismo directorio, cargar o rotar el secreto con el siguiente
   comando. Wrangler solicita el valor de forma interactiva. Este comando
   publica de inmediato una nueva versión del Worker activo. Para rotar,
   repetirlo con un nuevo valor aleatorio. Para revocar el acceso, usar
   `npx --yes wrangler@4.162.0 secret delete MACROPULSE_ADMIN_SECRET --config wrangler.jsonc`.
   [Cloudflare Worker Secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

   ```powershell
   npx --yes wrangler@4.162.0 secret put MACROPULSE_ADMIN_SECRET --config wrangler.jsonc
   ```

4. En la PWA, abrir **Administrator access** y seleccionar **Verify credential**.
   Comprobar que la nueva clave valida y, en una rotación, que la anterior
   falla. Con el consentimiento marcado, ejecutar una investigación de prueba.
   Salir del modo administrador y verificar que el flujo público pide Turnstile.
   Mantener las cabeceras de solicitudes fuera de cualquier registro de borde.

### Control de gasto

En Cloud Billing, crear un presupuesto con alertas al 50 %, 80 % y 100 % y,
si está disponible en la cuenta, habilitar el tope de gasto para Gemini API.
Seleccionar el proyecto dedicado de MacroPulse y configurar notificaciones
para los responsables de operación. Este control es independiente de las
reservas del Worker y cubre tanto el uso administrativo como el público.
Puede haber cargos durante la latencia de aplicación. Ante consumo anómalo,
rotar o revocar el secreto de inmediato. Si hace falta detener todo Research,
usar el interruptor global. [Topes y alertas de Cloud Billing](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)

## Evidencia documental

Render carga los 24 documentos y 47 afirmaciones de M7.B desde el repositorio.
El grafo usa TF-IDF, el recuperador predeterminado local, con los mismos filtros
de pertinencia, geografía, período y publicación. La fecha de publicación limita
la evidencia documental. Las observaciones usan la última captura disponible
y pueden contener revisiones posteriores al período investigado.

La inspección de `news` encontró 57 registros de EIA, 242 de la Reserva Federal
y 20 de GDELT. El perfil integra el artículo de EIA sobre la exposición
energética de Europa como contexto para HICP. Verifica los permisos vigentes
de su registro de origen y utiliza una paráfrasis revisada. El piloto histórico
autoriza el corpus original para inferencia local. Los textos y embeddings
existentes quedan fuera de las solicitudes a Gemini. Los dos boletines del BCE
añaden contexto bilateral de EUR/USD para COVID-19 y Ucrania. Para ampliar la
cobertura, agregar a `macropulse_online.news_review` únicamente afirmaciones
verificadas con URL canónica, fecha, alcance, atribución y derechos. Cuando
falta evidencia suficiente, Research devuelve la limitación o se abstiene.
[Artículo de EIA](https://www.eia.gov/todayinenergy/detail.php?id=51618) · [BCE 4/2020](https://www.ecb.europa.eu/press/economic-bulletin/html/eb202004.en.html) · [BCE 5/2022](https://www.ecb.europa.eu/press/economic-bulletin/html/eb202205.en.html)
