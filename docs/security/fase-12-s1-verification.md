# Fase 12.S1 · Verificación de integración

El Worker y Render usan el [contrato aprobado](fase-12-s1-contract.md). El origen estático autentica las lecturas. Para Research, el Worker emite una atestación HMAC vinculada a una reserva y Render solicita su consumo al Durable Object por un canal con credenciales separadas.

| Escenario | Resultado local | Cobertura |
| --- | --- | --- |
| Render directo sin firma o con el secreto estático | PASS | 403 antes de consultar el callback o ejecutar Research |
| Firma inválida, cuerpo, método, path, query o headers modificados | PASS | Rechazo en FastAPI. Los vectores de Node y Python coinciden, incluidos caracteres UTF-8 y terminaciones CRLF |
| Firma vieja o futura, nonce inválido o key ID retirado | PASS | Rechazo directo. La ventana de frescura incluye ambos bordes documentados |
| Replay secuencial, concurrencia y reinicio | PASS | Dos instancias FastAPI compiten por una misma reserva en workerd. Una devuelve 200, la otra 403. Tras reiniciar FastAPI y workerd con el mismo almacén, el replay sigue devolviendo 403 |
| Reserva ausente, liberada, expirada, de otro scope o consumida | PASS | Rechazos en las pruebas de transiciones y en la ruta de consumo. El caso de otro scope se verifica también en workerd |
| Turnstile inválido o cuota agotada | PASS | El Worker no envía una solicitud a Render. El runtime workerd comprueba el rechazo de Turnstile y admite ambos flujos válidos |
| Callback inaccesible, redirigido, limitado o con respuesta inesperada | PASS | 503 en FastAPI con cero ejecuciones o reintentos. Las pruebas verifican que el cliente exige TLS y que los dos límites del Worker rechazan antes del DO |
| Intento de admitir, vincular o liberar mediante la ruta HTTP | PASS | Las rutas externas de esas operaciones no existen. El callback accede solo a la transición de consumo |
| Clave HMAC filtrada y firma válida para otro cuerpo o scope | PASS | El callback llega al DO, que rechaza el compromiso. Se ejercita el flujo en workerd |
| Clave del callback filtrada y lease inventado | PASS | El callback autenticado recibe 403 en workerd. Una credencial capturada junto con el compromiso auténtico puede gastar la reserva una vez y causar una denegación de servicio |

La prueba con workerd ejecuta los módulos del Worker y el Durable Object real de SQLite y comprueba el callback de FastAPI. Usa transportes de prueba para el enlace HTTPS, respuestas sintéticas de Turnstile y un runner simulado. Las pruebas contra PostgreSQL desechable con TLS verificaron el flujo DEMO. Las nueve pruebas ONLINE pasaron en un contenedor PostgreSQL desechable con `pgvector`, accesible solo por loopback.

La aprobación de despliegue debe comprobar conectividad y TLS entre los hosts configurados, además de latencia y disponibilidad del callback. Los riesgos residuales incluyen abuso distribuido del endpoint, fallos ambiguos que agotan un lease y la carrera por una solicitud exacta capturada. El compromiso de un runtime con acceso al binding de cuota o a Gemini queda fuera de la garantía. [Modelo de amenazas y plan de migración](fase-12-s1-contract.md)
