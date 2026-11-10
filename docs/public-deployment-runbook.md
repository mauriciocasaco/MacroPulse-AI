# Runbook para desplegar la demo pública en Fase 4.4B

Estos pasos describen un despliegue futuro y requieren autorización explícita
para cada servicio y destino. La demostración inicial usa sólo series sintéticas.

## 1. Preparar artefactos locales

En la raíz, usar el entorno virtual del proyecto y ejecutar
`python -m pytest -ra`. En `frontend`, con la versión de Node de
`package.json`, ejecutar `npm ci`, `npm run lint`, `npm test`,
`npm run test:worker` y `npm run build:public`. Wrangler 4.36 o superior
requiere verificar su versión y configuración en la fase autorizada.
La aplicación se sirve en la raíz de su hostname HTTPS y consume `/api`
del mismo origen. Mantener el flujo de desarrollo de
[README](../README.md) y el [proxy local de Vite](../frontend/README.md).

## 2. Aprovisionar Supabase DEMO

1. Crear un proyecto dedicado con PostgreSQL 17 o superior y verificar en el dashboard su organización,
   entorno e identificador. Comparar con el proyecto DEV y comprobar que son
   distintos antes de abrir una conexión administrativa. No copiar backups,
   datos, usuarios, permisos o secretos de DEV.
2. Obtener la CA desde el panel de conexión del proyecto. Usar
   `sslmode=verify-full` y un cliente de PostgreSQL con `ON_ERROR_STOP`.
   Mantener las credenciales administrativas fuera del historial del shell.
   [TLS de Supabase](https://supabase.com/docs/guides/platform/ssl-enforcement).
3. Aplicar únicamente [synthetic.sql](../supabase/demo/synthetic.sql) sobre
   una base DEMO nueva. El script verifica la ausencia de esquemas del MVP,
   crea el dataset, revoca `CREATE` y `TEMPORARY` heredados de PUBLIC en
   la base DEMO y otorga sólo `CONNECT`, `USAGE` y `SELECT` al rol
   dedicado. Configurar la contraseña del rol con `\password marketpulse_demo_ro`
   en psql y habilitar `LOGIN` después. Usar una contraseña aleatoria
   generada en un gestor de secretos. Nunca colocarla en SQL, argumentos o
   logs.
4. Verificar con la sesión real del rol que sólo el esquema y la tabla
   esperados están accesibles para los datos de la demo. Confirmar ausencia
   de roles heredados, privilegios de creación, escrituras y acceso a
   funciones `SECURITY DEFINER` de usuario. Revisar privilegios por defecto
   y grants administrados por Supabase si el arranque rechaza el rol.
   Mantener `marketpulse_demo` fuera de los esquemas expuestos por PostgREST
   y sin grants a `anon` o `authenticated`.
5. Para Render sin IPv6 verificado, usar el pooler de sesión IPv4 en el
   puerto 5432 y el usuario compuesto por el rol y el identificador del
   proyecto. Confirmar que el pooler acepta el rol personalizado, verifica
   el hostname con la CA descargada y devuelve el rol de base esperado.
   [Conexiones de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres) ·
   [psql en Supabase](https://supabase.com/docs/guides/database/psql).

## 3. Aprovisionar Render

1. Revisar [render.yaml](../render.yaml) antes de importar el Blueprint.
   Usar la rama y cuenta aprobadas, el plan previsto y un solo proceso.
   El chequeo de salud por defecto es TCP. El API deliberadamente protege
   todas las solicitudes HTTP directas y no expone un health HTTP.
   [Blueprint](https://render.com/docs/blueprint-spec) · [Health checks](https://render.com/docs/health-checks).
2. Completar las variables `MARKETPULSE_MODE`, `MARKETPULSE_DEMO_PROJECT_REF`,
   `MARKETPULSE_DEMO_DSN`, `MARKETPULSE_DEMO_SSL_ROOT_CERT` y
   `MARKETPULSE_DEMO_ORIGIN_SECRET` con el perfil público. La DSN sólo
   incluye `host`, `port`, `dbname`, `user` y `password`; la aplicación
   impone TLS y timeout. Cargar la CA como Secret File
   `marketpulse-demo-ca.pem` y comprobar que se monta en la ruta absoluta
   indicada en el Blueprint. Render puede necesitar un ajuste manual de
   esta configuración. [Secret Files](https://render.com/docs/configure-environment-variables).
3. Generar el secreto de origen a partir de 32 bytes aleatorios y codificar
   en 64 caracteres hexadecimales minúsculos. Guardarlo como secreto del
   backend y del Worker. No configurar variables Gemini, DSN de DEV,
   configuración `PG*` ni caches de embeddings en Render. Conservar el
   análisis deshabilitado.
4. Verificar que el servicio arranca con el rol de sólo lectura. Confirmar
   que una solicitud HTTP directa sin secreto obtiene 403. No enviar el
   secreto en URL ni imprimirlo al hacer verificaciones.

## 4. Aprovisionar Cloudflare

1. Revisar [wrangler.jsonc](../frontend/wrangler.jsonc) desde `frontend`.
   Reservar IDs de namespace de rate limiting sin colisiones en la cuenta.
   Usar el build público y conservar `/api` y `/api/*` atendidos por el
   Worker antes de los archivos estáticos. Mantener el dominio público y
   las rutas bajo control del mismo equipo.
   [Static Assets](https://developers.cloudflare.com/workers/static-assets/binding/).
2. Configurar `MARKETPULSE_DEMO_ORIGIN_URL` con la URL HTTPS del servicio
   Render y cargar `MARKETPULSE_DEMO_ORIGIN_SECRET` mediante el panel de
   secretos o una entrada interactiva autorizada de Wrangler. Las variables
   de frontend, incluidas `VITE_*`, no deben contener credenciales.
   [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/).
3. Confirmar que `CF-Connecting-IP` llega del borde sin reemplazo por
   Pseudo IPv4. Evitar subrequests procedentes de Workers no confiables
   de la misma zona, porque Cloudflare puede derivar la IP de `x-real-ip`
   en ese contexto. Revisar retención de logs, Logpush, APM y captura de
   cabeceras en ambas plataformas. Desactivar el registro persistente de
   IP completas y los encabezados de autenticación del origen.
   [Cabeceras de Cloudflare](https://developers.cloudflare.com/fundamentals/reference/http-headers/).
4. Asociar el hostname aprobado y verificar TLS antes de compartirlo.
   Comprobar que una interrupción del origen deja el shell disponible.

## 5. Aceptación y reversión

- Confirmar tres series sintéticas, cobertura esperada, punto de índice
  visible, rutas sin caché, límites, `Retry-After`, manejo de 503/504,
  arranque en frío y PWA instalable.
- Verificar 403 en el origen sin secreto y ausencia de rutas de
  administración o documentación a través del Worker. Confirmar 503 para
  análisis sin consumo de Render, Gemini o base de datos.
- Registrar la revisión de [derechos](public-data-policy.md) y la prueba
  HTTPS de los servicios reales. Retener sólo evidencias sanitizadas.
- Para retirar la demo, desconectar el hostname del Worker y suspender el
  servicio Render desde sus paneles. Rotar el secreto de origen si se
  sospecha exposición. La rotación coordinada con secreto único puede
  interrumpir temporalmente el proxy. Cerrar el acceso al rol DEMO si se
  desactiva el servicio. Aplicar la reversión sólo a recursos de DEMO.
