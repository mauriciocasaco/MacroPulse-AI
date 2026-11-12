# Fase 8.2 · Contrato del backend DEMO compartido

## Selección del proyecto

| `MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT` | Referencia admitida | Aislamiento SQL |
| --- | --- | --- |
| Ausente o `false` | Proyecto DEMO separado. El validador excluye la referencia DEV. | Deben estar ausentes `macro_data`, `market_data` y `news`. |
| `true` | Exclusivamente `swpithkjviavycrxpwed`. | Los esquemas internos pueden existir. La conexión verifica que el rol DEMO carezca de acceso. |

El valor del flag distingue mayúsculas y minúsculas. Cualquier otro valor, referencia, identidad o destino invalida la configuración. En ambos modos la conexión pública admite el host directo `db.<ref>.supabase.co` con `marketpulse_demo_ro` o el pooler de sesiones `*.pooler.supabase.com` con `marketpulse_demo_ro.<ref>`. El destino siempre usa `postgres` y el puerto `5432`. El DSN contiene solo `host`, `port`, `dbname`, `user` y `password`. El backend agrega `sslmode=verify-full`, una ruta absoluta al archivo CA, `gssencmode=disable` y un límite de cinco segundos para conectar.

El proceso público requiere `MARKETPULSE_MODE=public_demo`, `MARKETPULSE_DEMO_PROJECT_REF`, `MARKETPULSE_DEMO_DSN`, `MARKETPULSE_DEMO_SSL_ROOT_CERT` y `MARKETPULSE_DEMO_ORIGIN_SECRET`. El secreto Worker → Render es de uso exclusivo para ese enlace. Las variables de conexión del explorador DEV, las credenciales privadas de Gemini y las opciones libpq `PG*` producen un error de configuración. El DSN de lectura se resuelve una sola vez a partir de las variables públicas. La ausencia del DSN o el fallo de la única conexión intentada produce un error cerrado.

## Verificación por conexión

El arranque, el catálogo, el historial y Research v2 abren la misma clase de conexión dedicada. Antes de cualquier lectura, cada conexión verifica TLS activo en modo `verify-full`, fija `search_path = pg_catalog`, aplica límites de sentencias, bloqueos y transacción, y abre una transacción `REPEATABLE READ READ ONLY`. `current_user` y `session_user` deben identificar la misma sesión de `marketpulse_demo_ro`. Se comprueban el permiso de iniciar sesión, la ausencia de atributos elevados, membresías salientes, escrituras y propiedad de objetos de aplicación.

En el modo compartido rige el [contrato de PostgreSQL de 8.2A](m8-shared-db-contract.md). El lector admite `TEMPORARY`, `USAGE` en `public`, las rutinas nativas habituales y las rutinas de extensión sujetas a los permisos del invocador. Admite las vistas oficiales de solo lectura de `pg_stat_statements`, objetos temporales propios y las capacidades nativas sobre objetos grandes propios y mensajes lógicos. El modo dedicado conserva sus controles previos sobre esquemas DEV, permisos temporales y rutinas accesibles.

La verificación compartida comprueba permisos efectivos sobre relaciones, columnas, secuencias y rutinas de aplicación, incluidos los grants de `PUBLIC` y las ACL latentes en esquemas sin `USAGE`. Rechaza escrituras y delegación sobre las tablas DEMO, ACL directas ajenas al contrato, callbacks de aplicación alcanzables por OID, acceso a estadísticas sin filtrar, credenciales, funciones privilegiadas, operaciones sensibles de `pg_net`, objetos grandes de terceros, servidores externos y permisos especiales sobre parámetros o tablespaces. El verificador administrativo de A conserva la comprobación de estructura e integridad de la migración y la autoridad sobre el aprovisionamiento.

Los dos perfiles exigen las dos tablas ordinarias permitidas, su lista exacta de columnas, el catálogo de series ECB y Eurostat esperado, cobertura, captura sin fechas futuras, un solo paquete de procedencia y los hashes y recuentos de la proyección. La lectura usa tablas calificadas y `ONLY`. La comprobación de integridad valida los datos frente a las huellas almacenadas por el propietario de la proyección.

## API pública y Research v2

El perfil público exige el secreto de origen y la IP verificada por Worker, aplica la lista de rutas y métodos permitidos y conserva los límites de consulta, cuerpo, ventana temporal, concurrencia, cuota y tiempo. FastAPI mantiene la selección de perfiles en el punto de entrada existente, y el launcher local conserva su lector y credenciales propios.

Research v2 toma todas las observaciones y operandos deterministas de una transacción DEMO verificada por investigación. Conserva Gemini, LangGraph, el corpus aprobado M7.B y TF‑IDF, los límites de tiempo y tokens, las citas, la validación de afirmaciones y la abstención. `MARKETPULSE_DEMO_RESEARCH_ENABLED=true` habilita Research con `MARKETPULSE_DEMO_GEMINI_API_KEY` y el modelo público permitido en `MARKETPULSE_DEMO_GEMINI_MODEL`. El catálogo y el historial pueden funcionar con Research deshabilitado.

## Verificación antes de activar el servicio público

1. Aprovisionar las tablas y el rol con la migración 8.2A y cargar el bundle con el SHA aprobado según el [runbook](public-deployment-runbook.md). Confirmar las huellas, ventanas y fechas de captura.
2. Generar una credencial exclusiva para `marketpulse_demo_ro`. Verificar como propietario y como lector las ACL efectivas, incluidos los objetos y operaciones sensibles de `pg_net`. El resultado de las pruebas locales con permisos sintéticos es independiente de la comprobación alojada, que sigue pendiente. Los privilegios que expongan información o credenciales DEV bloquean la activación.
3. Configurar el opt-in, la referencia, el DSN exclusivo y la CA del endpoint aprobado en Render. Comprobar el arranque y el rechazo de accesos directos. Verificar el catálogo y el historial a través del Worker antes de habilitar Research. Confirmar que las ACL y los accesos DEV se conservan.
4. Comprobar las rutas de autenticación y los permisos efectivos en las demás bases del proyecto. El backend fija `postgres` en su DSN y sus comprobaciones por conexión se aplican a esa base.

El proyecto compartido conserva los recursos y metadatos estándar de PostgreSQL. Los canales `LISTEN` y `NOTIFY` deben transportar únicamente información apta para los roles con acceso a la base. La identidad de aprovisionamiento y los propietarios de objetos forman parte del perímetro de confianza.
