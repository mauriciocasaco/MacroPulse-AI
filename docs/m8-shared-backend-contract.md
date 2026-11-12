# Fase 8.1 · Contrato del backend DEMO compartido

## Selección del proyecto

| `MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT` | Referencia admitida | Aislamiento SQL |
| --- | --- | --- |
| Ausente o `false` | Proyecto DEMO separado. El validador excluye la referencia DEV. | Deben estar ausentes `macro_data`, `market_data` y `news`. |
| `true` | Exclusivamente `swpithkjviavycrxpwed`. | Los esquemas internos pueden existir. La conexión verifica que el rol DEMO carezca de acceso. |

El valor del flag distingue mayúsculas y minúsculas. Cualquier otro valor, referencia, identidad o destino invalida la configuración. En ambos modos la conexión pública admite el host directo `db.<ref>.supabase.co` con `marketpulse_demo_ro` o el pooler de sesiones `*.pooler.supabase.com` con `marketpulse_demo_ro.<ref>`. El destino siempre usa `postgres` y el puerto `5432`. El DSN contiene solo `host`, `port`, `dbname`, `user` y `password`. El backend agrega `sslmode=verify-full`, una ruta absoluta al archivo CA, `gssencmode=disable` y un límite de cinco segundos para conectar.

El proceso público requiere `MARKETPULSE_MODE=public_demo`, `MARKETPULSE_DEMO_PROJECT_REF`, `MARKETPULSE_DEMO_DSN`, `MARKETPULSE_DEMO_SSL_ROOT_CERT` y `MARKETPULSE_DEMO_ORIGIN_SECRET`. El secreto Worker → Render es de uso exclusivo para ese enlace. Las variables de conexión del explorador DEV, las credenciales privadas de Gemini y las opciones libpq `PG*` producen un error de configuración. El DSN de lectura se resuelve una sola vez a partir de las variables públicas. La ausencia del DSN o el fallo de la única conexión intentada produce un error cerrado.

## Verificación por conexión

El arranque, el catálogo, el historial y Research v2 abren la misma clase de conexión dedicada. Antes de cualquier lectura, cada conexión verifica TLS activo, fija `search_path = pg_catalog`, aplica los límites de sentencias, bloqueos y transacción, y abre una transacción `REPEATABLE READ READ ONLY`. `current_user` y `session_user` deben identificar la misma sesión de `marketpulse_demo_ro`. Se comprueban el permiso de iniciar sesión, la ausencia de atributos elevados, membresías, permisos de creación y temporales, escrituras y propiedad de objetos. La comprobación incluye el acceso efectivo a rutinas fuera de los catálogos del sistema.

El modo compartido comprueba además el alcance efectivo y las concesiones explícitas del rol en el catálogo, incluidas concesiones latentes en esquemas inaccesibles y privilegios heredados de `PUBLIC`. Los permisos de aplicación admisibles son `CONNECT` en la base, `USAGE` en `marketpulse_demo` y `SELECT` de tabla sobre `macro_catalog` y `macro_observations`, sin opción de concederlos. Se rechazan `CREATE` en cualquier esquema, acceso a esquemas ajenos fuera de los catálogos estándar, acceso a secuencias y concesiones explícitas de `SET` o `ALTER SYSTEM` sobre parámetros de PostgreSQL.

La verificación también rechaza concesiones adicionales directas, acceso a servidores o wrappers foráneos, ACL de objetos grandes accesibles, `lo_compat_privileges` activo, rutinas u objetos de sistema agregados con acceso público y ampliaciones de las ACL iniciales en las relaciones y rutinas de sistema. El rol debe carecer de `EXECUTE` efectivo en `lo_create`, `lo_creat`, `lo_from_bytea` y `pg_logical_emit_message`, incluidas sus sobrecargas. Esas rutinas permiten persistir objetos grandes o mensajes WAL fuera de la lectura de aplicación.

Los dos perfiles exigen las dos tablas ordinarias permitidas, su lista exacta de columnas, el catálogo de series ECB y Eurostat esperado, cobertura, captura sin fechas futuras, un solo paquete de procedencia y los hashes y recuentos de la proyección. La lectura usa tablas calificadas y `ONLY`. La comprobación de integridad valida los datos frente a las huellas almacenadas por el propietario de la proyección.

## API pública y Research v2

El perfil público exige el secreto de origen y la IP verificada por Worker, aplica la lista de rutas y métodos permitidos y conserva los límites de consulta, cuerpo, ventana temporal, concurrencia, cuota y tiempo. FastAPI mantiene la selección de perfiles en el punto de entrada existente, y el launcher local conserva su lector y credenciales propios.

Research v2 toma todas las observaciones y operandos deterministas de una transacción DEMO verificada por investigación. Conserva Gemini, LangGraph, el corpus aprobado M7.B y TF‑IDF, los límites de tiempo y tokens, las citas, la validación de afirmaciones y la abstención. `MARKETPULSE_DEMO_RESEARCH_ENABLED=true` habilita Research con `MARKETPULSE_DEMO_GEMINI_API_KEY` y el modelo público permitido en `MARKETPULSE_DEMO_GEMINI_MODEL`. El catálogo y el historial pueden funcionar con Research deshabilitado.

## Requisitos para la Fase 8.2

1. Aprovisionar la proyección y el rol en el proyecto autorizado con el SQL del worktree A. Cargar una proyección aprobada, sellada y con procedencia verificable mediante un procedimiento autorizado para el destino compartido. El importador del perfil separado conserva su validación de destino original.
2. Generar una credencial exclusiva para `marketpulse_demo_ro` y confirmar su independencia de DEV durante el aprovisionamiento. Confirmar las ACL efectivas con la identidad de inicio de sesión, incluidas las concesiones de `PUBLIC` en esquemas administrados y los permisos de rutinas que escriben objetos grandes o WAL. Preservar el acceso de los roles legítimos de DEV al ajustar concesiones compartidas.
3. Confirmar con un propietario autorizado que Supabase permite aplicar las revocaciones necesarias. El rol administrado `postgres` tiene privilegios limitados y las revocaciones sobre objetos administrados pueden ser ineficaces. Si el proveedor impide alcanzar el alcance exigido, el arranque compartido debe seguir bloqueado. [Roles administrados de Supabase](https://supabase.com/docs/guides/database/postgres/roles-superuser) · [Permisos sobre objetos administrados](https://supabase.com/kb/troubleshooting/revoking-privileges-on-storage-pg_cron-vault-and-extension-objects-has-no-effect-916576).
4. Configurar el flag, la referencia, el DSN exclusivo y la CA del endpoint aprobado en Render. Comprobar el arranque, el rechazo de accesos directos, el catálogo, el historial y Research a través del Worker autorizado. Verificar que una conexión con la credencial pública reciba una denegación de PostgreSQL al intentar leer una tabla interna y que el acceso de DEV siga operativo.
5. Inventariar las demás bases de usuario del proyecto. Comprobar con la credencial pública que el servidor deniega la conexión a cualquier otra base con datos DEV o internos, incluidas las que concedan `CONNECT` a `PUBLIC` por defecto. El backend fija `postgres` en su DSN y sus comprobaciones de objetos se aplican a esa base.

El proyecto compartido conserva recursos y metadatos estándar del servidor. Los canales PostgreSQL de `LISTEN` y `NOTIFY` pertenecen a la base compartida y deben transportar únicamente información apta para los roles que pueden conectarse. La identidad de aprovisionamiento y los propietarios de objetos siguen formando parte del perímetro de confianza. La verificación local utiliza datos sintéticos y un clúster PostgreSQL desechable en loopback. La disponibilidad de los permisos necesarios en Supabase administrado requiere la comprobación de la Fase 8.2.
