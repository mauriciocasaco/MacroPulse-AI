# Fase 8.2 — Plan de despliegue con Supabase compartido

El baseline de Fase 8.1 es `8eedcb3`. El destino previsto es el proyecto
Supabase `swpithkjviavycrxpwed`, base `postgres`, con el esquema
`marketpulse_demo` y el rol de login `marketpulse_demo_ro`. La aplicación
pública usa las dos tablas de la proyección DEMO con TLS `verify-full`.
DEV y DEMO comparten capacidad de PostgreSQL. La separación de datos depende
del alcance efectivo del rol, incluidos los permisos otorgados a `PUBLIC`.

El [runbook de despliegue](public-deployment-runbook.md) contiene el
procedimiento operativo. El cambio de infraestructura local se prepara en
`feat/m8-shared-deployment`. La promoción requiere un SHA integrado y
autorización para operar en cada destino.

## Contratos pendientes de integración

| Trabajo | Contrato esperado | Evidencia que debe acompañar la integración |
| --- | --- | --- |
| A — Base de datos | Migración y reversión acotadas a objetos DEMO. Esquema fuera de PostgREST, rol dedicado sin membresías ni propiedad, dos tablas de lectura, carga de las cinco series y restauración protegida por SHA activo. | Prueba con esquemas DEV sintéticos coexistentes, comparación de ACL antes y después, inventario efectivo del rol DEMO y procedimiento de carga, revocación y restauración verificado. |
| B — Backend | El perfil público requiere el opt-in literal `MARKETPULSE_DEMO_ALLOW_SHARED_PROJECT=true` para la referencia compartida. Valida DSN del rol DEMO directo o por pooler de sesión, TLS `verify-full`, identidad efectiva, alcance de solo lectura e integridad de la proyección. Conserva el secreto Worker → Render, las rutas y la disponibilidad condicionada de Research v2. | Pruebas de rechazo con opt-in ausente o incorrecto, referencia/rol incorrectos, TLS inválido y alcance excesivo. Prueba con la proyección de A y esquemas DEV coexistentes. Confirmación conjunta del mecanismo y CLI de carga administrativa compartida. |
| C — Infraestructura | Blueprint de Render con referencia fijada, opt-in explícito y Research apagado. CA en archivo secreto, DSN y secreto de origen inyectados en Render. La clave Gemini se carga durante 8.2D. Worker y Durable Object conservan su contrato de Fase 8.1. | Validación local del Blueprint, configuración Worker, PWA pública, pruebas del Worker y referencias del runbook. |

En `8eedcb3`, el validador de destino rechaza la referencia compartida, el
lector y el importador requieren ausencia de esquemas DEV y el SQL dedicado
incluye revocaciones globales de `PUBLIC`. Las variables de Render preparan el
contrato con B. La compatibilidad de A y B, incluido el caso de privilegios
efectivos concedidos a `PUBLIC`, se debe demostrar antes de arrancar el origen.

## Orden de promoción

| Etapa | Condición para avanzar | Evidencia operativa |
| --- | --- | --- |
| 8.2A — Supabase | Destino, respaldo, migración y alcance efectivo aprobados. | Inventario antes y después, TLS, dos tablas accesibles por el rol DEMO, cinco series con manifiesto y SHA-256 y verificación de accesos DEV. |
| 8.2B — Render | Proyección validada y rol de lectura habilitado. | Origen autenticado responde con las cinco series, acceso directo sin secreto devuelve 403 y Research sigue apagado. |
| 8.2C — Cloudflare | Origen y configuración de borde verificados. | Hostname HTTPS, catálogo, históricos, gráficos y PWA móvil operativos. Durable Object y Turnstile configurados. Research sigue apagado. |
| 8.2D — Gemini y Research v2 | Cuenta, clave, facturación y controles de uso aprobados. | Desafío real, consulta con referencias, abstención, rechazo de replay, límites y apagado comprobados. |

El presupuesto de la aplicación es USD 10 estimados por mes UTC, con 100
reservas de USD 0,10. Google mide y factura por separado. Render y Cloudflare
pueden sumar costos según sus planes y uso. Registrar en la promoción las
condiciones de las cuentas y la capacidad compartida de Supabase.

Registrar el SHA, huella de la migración, respaldo, huella del bundle, versión
de Render y Worker, fecha UTC y resultados sanitizados por etapa. Ante un
fallo, ejecutar la reversión específica del runbook y verificar el servicio
DEV. La validación local de C usa fixtures y artefactos locales. Las pruebas
reales de Supabase, Render, Cloudflare, Turnstile y Gemini quedan pendientes
para la ventana de despliegue autorizada.
