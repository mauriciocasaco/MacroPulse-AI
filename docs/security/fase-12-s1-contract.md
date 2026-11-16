# Fase 12.S1 · Contrato de seguridad Worker → Render

Estado: Gate A aprobado. Base local `dev` en `0d80bd29c0aaf1c4f06e559c8e42efa9b9694b27`.

## Hallazgo y alcance

El Worker aplica Turnstile y cuotas para Research público. La vía administrativa verifica una credencial propia y usa su propio presupuesto. Ambas vías reservan un lease de 90 s en `ResearchQuota`. En la base revisada, Render acepta el secreto estático de origen y comprueba que el deadline aportado por el cliente esté cerca del presente. La comprobación ignora si el lease existe, pertenece a esa solicitud o ya financió una ejecución.

Este protocolo cubre toda llamada a `POST /macro/analysis` en el origen, incluidas las vías pública y administrativa. El catálogo y los históricos oficiales conservan su autenticación y sus límites actuales. El secreto estático queda autorizado exclusivamente para esas lecturas. Su filtración conserva el riesgo de evasión del límite del Worker para lecturas, sujeto a los límites de Render. El endpoint de Research nunca lo acepta como credencial.

## Secuencia de autorización

1. El Worker aplica los controles actuales, valida el JSON y obtiene una reserva exitosa del Durable Object. Elige la instancia pública o administrativa en función de la admisión ya realizada. Comprueba la configuración de atestación antes de gastar una reserva. El chequeo de credencial administrativa que devuelve 204 por sí solo carece de autorización de Research.
2. El Worker serializa la operación, deriva un compromiso SHA-256 sobre esos bytes y vincula el compromiso a la reserva mediante una llamada interna en el binding existente `RESEARCH_QUOTA`. Solo tras recibir 204 firma la operación y la envía una vez por HTTPS a la raíz Render configurada, con redirecciones deshabilitadas.
3. FastAPI verifica los límites de entrada y la HMAC sobre la operación recibida. Si Research está habilitado y queda presupuesto de ejecución, llama por HTTPS al endpoint de consumo de Cloudflare. Toda instancia Render de ese entorno usa el mismo host de verificación y las mismas instancias lógicas de cuota. El Worker verifica una credencial exclusiva del canal de retorno y solo entonces pide al Durable Object consumir la reserva correspondiente.
4. Render ejecuta Research únicamente tras recibir 204 antes del timeout y confirmar que todavía queda presupuesto. La autorización precede al parser de Research, a las consultas y al runner de Gemini. Una respuesta perdida después de consumir la reserva implica que esa reserva se pierde. Un intento HTTP independiente requiere una admisión nueva.

La decisión reutiliza el almacenamiento SQLite de `ResearchQuota`. Cada instancia mantiene su propio presupuesto y estado persistente. Cloudflare documenta la identidad global de cada objeto, el almacenamiento transaccional y el rollback de `transactionSync` ante una excepción. [Durable Objects](https://developers.cloudflare.com/durable-objects/concepts/what-are-durable-objects/) · [API SQLite](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#transactions)

## Protocolo de petición a Render

Verificar cada header mediante la lista cruda ASGI. Debe existir exactamente un valor por campo. Rechazar valores combinados con comas o que incumplan la gramática, incluidos whitespace y controles. Los nombres de headers son insensibles a mayúsculas. La validación sintáctica se aplica al valor disponible en el límite ASGI tras el procesamiento HTTP del proxy.

| Header | Valor |
| --- | --- |
| `X-MacroPulse-Origin-Key-Id` | `[a-z0-9][a-z0-9_-]{0,31}` |
| `X-MacroPulse-Origin-Timestamp` | Unix en milisegundos, 13 dígitos ASCII, primero distinto de cero |
| `X-MacroPulse-Origin-Nonce` | ID UUIDv4 canónico en minúsculas generado por `crypto.randomUUID()` en el DO al admitir. Cada reserva genera su propio nonce |
| `X-MacroPulse-Origin-Scope` | Literal `public` o `admin` |
| `X-MacroPulse-Origin-Signature` | HMAC-SHA256 en 64 caracteres de hex minúsculo |
| `X-MacroPulse-Research-Expires` | Vencimiento exacto devuelto por el DO, mismo formato que el timestamp |
| `X-MacroPulse-Research-Id` | UUIDv4 canónico para correlación, independiente del nonce |
| `X-MarketPulse-Client-IP` | Valor ASCII del Worker con gramática `[0-9a-fA-F:.]{1,45}` y dirección IPv4 o IPv6 válida. Firmar el texto exacto y normalizar solo después de verificar |
| `Content-Type` | Literal `application/json`, sin parámetros |

El método debe ser `POST`. El path crudo ASGI debe ser exactamente `/macro/analysis` y coincidir con el path decodificado. La única query canónica permitida es vacía, representada por una línea vacía. Cualquier byte en `query_string` invalida la solicitud. HTTP y ASGI presentan el separador vacío `?` y la ausencia de query con los mismos bytes de query. El Worker emite una URL sin separador. Un path codificado, alternativo o con barra final se rechaza. `Content-Encoding` debe estar ausente. Se conservan la validación de framing, el tope de 4096 bytes y el timeout de lectura de 3 s. Render obtiene el hash del cuerpo recibido sin parsearlo ni volver a serializarlo. El Worker produce una única secuencia de bytes tras la validación y usa esa misma secuencia para hashear y enviar.

Los bytes canónicos son exactamente estas líneas ASCII en este orden, separadas por LF (`0a`), incluida la línea vacía de query. No hay LF final:

```text
macropulse/render-research/v1
<key-id>
POST
/macro/analysis

application/json
<sha256-del-cuerpo-en-hex-minúsculo>
<timestamp-ms>
<nonce>
<scope>
<expires-ms>
<research-id>
<client-ip>
```

Las gramáticas impiden delimitadores dentro de los campos. La firma usa los bytes de la clave decodificada de hex, con HMAC-SHA256 sobre el mensaje canónico. El compromiso usa SHA-256 del mismo mensaje canónico, codificado como hex minúsculo. Render selecciona exclusivamente la clave indicada y compara los 32 bytes esperados y recibidos con `hmac.compare_digest`. Web Crypto soporta HMAC y SHA-256 en el Worker. [Cloudflare Web Crypto](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/)

La firma se acepta cuando `now_ms - 10_000 <= timestamp_ms <= now_ms + 2_000`. Render verifica con su reloj después de leer el cuerpo. El DO aplica la misma ventana con su reloj al vincular y al consumir. El deadline debe ser posterior al timestamp. Al enviar, el Worker conserva los 70 s de timeout y exige al menos 72 s de lease restante. Render conserva los 65 s de ejecución y su margen de lease de 2 s, con la validación actual del máximo de 95 s restantes. Verifica ese margen otra vez tras el consumo y antes de iniciar trabajo. El límite de 10 s se aplica a autorizar el inicio. Una investigación ya autorizada puede usar su presupuesto de ejecución de 65 s.

## Binding y consumo

El Worker llama al mismo objeto que emitió la reserva. La ruta interna `POST /bind-origin` recibe JSON con `id`, `commitment` (64 caracteres hex minúsculo), `issuedAt` y `expires` (enteros de milisegundos). `POST /consume-origin` recibe los mismos cuatro campos. Los dos devuelven 204 al confirmar y 403 si no se cumple la transición. Cuerpo o estructura inválidos devuelven 400. Estas rutas solo se alcanzan por el binding privado. El endpoint externo implementa exclusivamente consumo.

Usar una tabla auxiliar `origin_grants` con una fila como máximo por lease, indexada por ID, que guarde compromiso, timestamp y estado de consumo. Crear la tabla idempotentemente al inicializar el DO y conservar los nombres de instancia y tablas de cuota existentes. Los leases anteriores sin vínculo no dan autorización por sí solos.

| Estado | Transición válida |
| --- | --- |
| Reservada | `/bind-origin` crea el único vínculo para un lease existente, vigente y con expiración exacta. Una segunda vinculación falla, incluso con el mismo compromiso |
| Vinculada | `/consume-origin` verifica ID, compromiso, timestamp vinculado, expiración exacta, vigencia y estado pendiente. Cambia el estado a consumido y exige que la operación afecte exactamente una fila |
| Consumida | Conserva el lease activo para contar concurrencia hasta `/release` o expiración. Toda llamada adicional de consumo falla |
| Liberada o expirada | Elimina el lease y su vínculo. El consumo exige que exista el lease aunque el alarm se haya retrasado |

Las comprobaciones y escrituras de cada transición ocurren dentro de una única `transactionSync`, sin `await`. Liberación y limpieza borran el vínculo y el lease juntos en transacción. Deben limpiar también vínculos huérfanos si una versión anterior llegó a liberar un lease mientras convivía con la tabla nueva. La tabla solo crece a partir de leases ya contabilizados. El alarm y la limpieza de admisión usan la expiración original. El consumo fallido nunca inserta estado. Liberar un lease no resta una admisión de las cuotas diarias, mensuales o por visitante.

Si release gana la carrera, consume falla. Si consume gana, la autorización irrevocable ya se otorgó. El Worker libera anticipadamente solo ante una respuesta Render marcada como completa o ante un fallo previo en el que sabe que la solicitud no salió. Una transmisión o verificación con resultado incierto conserva el lease hasta que venza. La confirmación del DO debe emitirse después de confirmar la escritura y respetar el output gate de Cloudflare. El proceso Render no reintenta el callback ni reutiliza su 204 en otra operación. [Semántica de almacenamiento y concurrencia](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)

Instancias existentes por entorno:

| Scope | Nombre |
| --- | --- |
| `public` | `macropulse-research-global-v1` |
| `admin` | `macropulse-research-admin-budget-v1` |

El callback elige el DO exclusivamente a partir del scope permitido. Nunca enruta al objeto de fallos de autenticación. Las reservas se verifican por identidad y compromiso, sin confiar en un contador, caché o set local en Render.

## Callback Render → Cloudflare

Usar `POST /api/internal/research/consume-v1` en el hostname exacto configurado para el Worker de Research. La ruta se despacha antes de las rutas públicas y antes del interruptor de Research, de modo que Render puede verificarla durante el corte. El host debe estar configurado localmente en ambos extremos. Render construye el URL fijo con HTTPS, sin puerto explícito, usuario, query, fragmento ni host pedido por el cliente. TLS usa CA de confianza y validación de hostname. El cliente HTTP deshabilita redirecciones, proxies de entorno y reintentos. Nunca reenvía headers entrantes. El Worker comprueba HTTPS, host exacto, método, query vacía y ausencia de browser CORS. No emite headers CORS ni sirve assets en esta ruta.

| Header | Valor |
| --- | --- |
| `X-MacroPulse-Verify-Key-Id` | Gramática de key ID del protocolo |
| `Authorization` | Literal `Bearer ` seguido de 64 caracteres hex minúsculos de una clave exclusiva para consumo |
| `Content-Type` | Literal `text/plain` |

Un header repetido que Fetch entregue combinado se rechaza por gramática. No se acepta `Content-Encoding`. El cuerpo tiene como máximo 192 bytes y usa cinco líneas ASCII separadas por LF, sin LF final. De arriba a abajo contiene scope, nonce, compromiso, timestamp y expiración, con las gramáticas anteriores. Ambos extremos marcan la operación como `no-store`. No se devuelven el compromiso ni datos de la reserva.

Aplicar en el Worker un binding dedicado `RESEARCH_VERIFY_RATE_LIMIT` con 30 intentos por minuto por IP de conexión antes de acceder al DO. Después de autenticar, aplicar un segundo límite de 30 por minuto por key ID con una clave de contador de prefijo diferente, para limitar el uso del mismo token desde varias IP en una ubicación. El primer límite usa la IP validada de `CF-Connecting-IP`. Los contadores de verificación son independientes de los de Research público y administrativo. Cloudflare limita por ubicación y con contadores aproximados. La autorización y el consumo dependen de la transacción, incluso bajo abuso distribuido. Sin binding o con error del limitador, responder 503. Usar comparación de tiempo constante del token decodificado con `crypto.subtle.timingSafeEqual`. [Cloudflare Rate Limiting](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/) · [Cloudflare Web Crypto](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/#timingsafeequal)

Limitar la lectura del callback a 500 ms y su subrequest al DO a 1500 ms. El cliente de Render usa una única llamada con deadline total de 2500 ms, incluidos establecimiento de conexión y recepción de headers. Acepta solo 204 del endpoint TLS esperado, sin redirecciones y sin caché. Consultar los headers de respuesta sin descargar un cuerpo arbitrario.

| Resultado | Callback | Render / Worker público |
| --- | --- | --- |
| Autorización aceptada | 204 solo tras consumo atómico | Render continúa. Se conserva el contrato público |
| Credencial distinta, inválida o retirada, reserva inexistente, consumida, liberada, expirada o con discrepancias | 403 genérico | Render devuelve 403 genérico para credenciales de origen inválidas o negativa de consumo. El Worker público conserva su error genérico para el 403 del origen |
| Formato, tamaño, método o tipo inválidos | 400, 413, 405 o 415 según el caso | Render conserva sus errores de validación y rechaza cualquier callback inesperado |
| Limitador de verificación agotado | 429, sin acceso al DO | Render devuelve 503 genérico y no ejecuta |
| Claves de verificación sin configurar, DO inaccesible, timeout, redirección, error de TLS o error interno | 503 o fallo de transporte | Render devuelve 503 genérico y no ejecuta |
| Turnstile fallido, cuota/budget agotados o admin inválido | Sin callback | El Worker conserva los rechazos públicos y administrativos existentes |

## Configuración y rotación

| Nombre | Worker | Render |
| --- | --- | --- |
| `MACROPULSE_RESEARCH_ORIGIN_KEYS` | Secreto, claves de emisión | Secreto, claves de verificación |
| `MACROPULSE_RESEARCH_ORIGIN_ACTIVE_KID` | ID de la clave de emisión | — |
| `MACROPULSE_RESEARCH_VERIFY_KEYS` | Secreto, claves de verificación del callback | Secreto, claves de emisión del callback |
| `MACROPULSE_RESEARCH_VERIFY_ACTIVE_KID` | — | ID de la clave de emisión del callback |
| `MACROPULSE_RESEARCH_HOSTNAME` | Host existente de Research y callback | Host de confianza del callback |
| `RESEARCH_VERIFY_RATE_LIMIT` | Binding dedicado, ID de namespace exclusivo a asignar en integración | — |

Los anillos son arrays JSON de uno o dos pares `[kid, key_hex]`, con IDs únicos y claves de 32 bytes aleatorios de CSPRNG, en hex minúsculo. Los pares evitan ambigüedad por miembros JSON duplicados. Validar formato, tamaños, membresía del activo y ausencia de material repetido dentro de cada anillo y entre ambos. Generar material independiente del secreto estático, Turnstile, hash de visitantes y credencial administrativa. Usar claves separadas por entorno. Render y Worker fallan en cerrado para Research ante configuración inválida. Las lecturas y el chequeo administrativo de credenciales se mantienen operativos. El catálogo solo anuncia Research si el origen lo anuncia y el Worker verifica configuración y disponibilidad local de cuota.

En cada canal, instalar primero la nueva clave en el verificador sin retirar la anterior, activar luego el nuevo ID en el emisor y confirmar que todas sus réplicas lo usan. Para rotación ordinaria, conservar la clave anterior al menos 90 s después de la última emisión posible y retirarla en ambos lados. Ante compromiso, revocar de inmediato y suspender Research si aún no hay una pareja emisor/verificador que coincida. Un ID retirado tampoco se acepta para una reserva que ya estaba vinculada.

## Amenazas y verificación exigida en integración

| Condición | Resultado esperado | Evidencia exigida |
| --- | --- | --- |
| Render directo sin credenciales o con secreto anterior | Rechazo antes del runner | Invocar FastAPI y contar llamadas a consultas y Gemini |
| Firma falsa, cuerpo, método, path, query, scope, IP, deadline o ID alterados | Rechazo | Probar mutaciones sobre la misma firma. Probar path crudo codificado y headers duplicados |
| Fecha vieja o futura, nonce malformado, ID retirado | Rechazo | Probar ambos bordes de tiempo, claves activas/salientes y rotación |
| Replay secuencial o concurrente, después de reiniciar Render o desde dos instancias | Como máximo una ejecución | Dos apps Render con el mismo DO persistente. Repetir con recreación del DO sobre su almacenamiento. Una simulación en memoria no acredita persistencia |
| Reserva inexistente, liberada, expirada, de otro scope o ya consumida | Rechazo | DO real local con SQLite. Probar carrera con release, fallo antes del commit y pérdida de respuesta después del commit |
| Turnstile rechazado o cuota agotada | Sin bind, firma ni envío a Render | Pruebas Worker para vía pública y vía administrativa |
| Callback inaccesible o redirigido, respuesta inesperada, TLS no válido o rate limit | Research no se ejecuta | Cliente de transporte y ruta Worker con fallos sintéticos |
| Intento de admitir, vincular o liberar desde la ruta externa | Sin cambio de estado | Probar ruta y método externos, incluso con credencial de verificación válida |
| Clave de firma filtrada, HMAC válida sobre reserva inexistente o compromiso distinto | Rechazo por estado o compromiso | Generar una firma válida con la clave de prueba y comprobar el rechazo del DO |
| Clave de callback filtrada | No permite emitir firmas ni crear reservas. Puede intentar consumir un compromiso exacto capturado y provocar denegación de servicio | Probar autenticación de callback sin vínculo válido y consumo de un vínculo auténtico con la credencial de prueba |

Una HMAC válida es falsificable con la clave de firma. La barrera adicional es la escritura previa del compromiso por la ruta de admisión que dispone del binding privado. Con acceso a la operación exacta ya vinculada, un atacante puede ganar la carrera por la única autorización y ejecutar o bloquear esa investigación. No obtiene una segunda ejecución ni puede cambiar el contenido conservando el compromiso. Un compromiso del Worker con acceso a `RESEARCH_QUOTA` o del runtime de Render con acceso a Gemini queda fuera de la garantía. Una credencial administrativa filtrada conserva acceso dentro de su presupuesto. La denegación de servicio distribuida, la pérdida de leases ante fallos ambiguos, los desfases de reloj y la latencia adicional del callback son riesgos residuales.

Los logs solo incluyen ID de correlación y campos operativos fijos. No registran secretos, firmas, nonce de reserva, compromisos, payloads ni errores crudos. Usar los fixtures sintéticos en [tests/fixtures/security](../../tests/fixtures/security). La matriz documenta resultados esperados. La evidencia de implementación queda pendiente de B, C, D e integración.

## Validación del diseño en Gate A

- Python generó dos vectores sintéticos. Node Web Crypto reconstruyó ambos mensajes, verificó hashes, firmas y callbacks, rechazó 18 mutaciones de los campos firmados y confirmó los cinco casos temporales por vector.
- Una prueba del predicado transaccional con SQLite local admitió uno de ocho consumidores concurrentes. El replay desde una conexión nueva, una reserva liberada, una inexistente, una expirada y los cambios de compromiso o expiración se rechazaron. Un fallo antes de confirmar la escritura permitió consumir una sola vez en el intento posterior.
- La prueba SQLite valida el invariante propuesto bajo concurrencia local. La persistencia y el enlace entre Worker, Durable Object y FastAPI se deben demostrar en la implementación.

## Migración y rollback

1. Validar el contrato en CI y en una topología no productiva con ambos componentes, el binding SQLite y credenciales sintéticas o propias de ese entorno. Medir latencia del bind y callback, y verificar que el Worker conserva admisión, caché, concurrencia y budgets. Usar promoción revisada `dev → staging → prod` y despliegue manual.
2. Preparar ambos anillos y los IDs activos en los gestores de Cloudflare y Render. Confirmar host, TLS y binding específico. Mantener Research desactivado en las revisiones nuevas mientras se prepara el cambio.
3. En el corte, bloquear primero tráfico público a Render con Maintenance Mode en el servicio de pago y comprobar el 503 desde una sonda externa. Esto interrumpe temporalmente las lecturas que necesiten origen. Confirmar que no haya una ruta privada o alternativa de ingreso habilitada para clientes no confiables. Apagar Research en el Worker y en Render, verificar el despliegue efectivo y esperar 90 s desde la última admisión posible. Si la capacidad de bloquear el origen para el corte no está disponible, detener publicación hasta disponer de una barrera equivalente. Apagar únicamente el Worker deja abierto el bypass en la revisión anterior. [Render Maintenance Mode](https://render.com/docs/maintenance-mode) · [TLS en Render](https://render.com/docs/tls)
4. Desplegar el Worker con el endpoint de consumo y conservar sus instancias DO. Desplegar el origen con verificación estricta y Research apagado. Confirmar que todas las instancias antiguas estén fuera de servicio antes de reabrir tráfico Render. Verificar catálogo, históricos y rechazos directos con Research inactivo.
5. Activar primero Research en Render estricto. Confirmar que acceso directo sin firma o con el secreto anterior falla incluso estando habilitado. Activar después Research en el Worker y verificar disponibilidad de catálogo, admisión pública y administrativa, respuesta válida y rechazo de replay en el entorno autorizado.
6. Para rollback, apagar Research en ambos lados y confirmar drenaje. Si se vuelve a una revisión de Render anterior al protocolo, volver a bloquear el origen antes de sustituirla y mantener Research explícitamente desactivado en todas las instancias al reabrir. Mantener las cuotas persistentes y retirar claves comprometidas. Reactivar Research únicamente con una pareja que exija firma y consumo único en ambos sentidos.
