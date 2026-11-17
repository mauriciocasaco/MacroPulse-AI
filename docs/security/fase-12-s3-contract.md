# Fase 12.S3 — Contrato de seguridad (Gate 0)

Base verificada tras `git fetch origin`: `b6e6abde261ae14d29085aae6dd63e9003e8f6a2` de `origin/dev`. La PR [#34](https://github.com/mauriciocasaco/MacroPulse-AI/pull/34) está integrada mediante `ad303c37e828da9d7820130fe258df3b5f16d907`. La PR [#43](https://github.com/mauriciocasaco/MacroPulse-AI/pull/43) cerró sin merge y su punta de 12.S2 queda fuera de la ascendencia de la base.

## Admisión de Research

- Una función determinista `contains_sensitive_input(text: str, *, secrets: Iterable[str] = ()) -> bool` en `backend/macro_ai/sensitive_input.py` clasifica credenciales de formato reconocible, valores asignados explícitamente como contraseña o token, autorizaciones Bearer y Basic, marcadores PEM de clave privada y URI con usuario y contraseña. También clasifica emails, teléfonos claramente formateados e identificadores financieros inequívocos. Para números susceptibles de confundirse con datos económicos, exigir formato, contexto explícito o checksum según el tipo.
- Comparar valores configurados y aplicar NFKC y tratamiento acotado de separadores e invisibles solo sobre la copia de detección. Mantener la pregunta admitida tal como la entregue el parser actual. Los valores configurados incluyen la clave Gemini, secretos de origen y atestación y contraseña de la DSN donde estén disponibles. Omitir valores vacíos. La función devuelve solo un booleano.
- Para el origen público y administrativo, conservar la autenticación, la verificación HMAC sobre los bytes recibidos y el consumo único de 12.S1. Después del consumo, inspeccionar la pregunta antes del runner, de cualquier consulta de la petición a PostgreSQL y de toda operación Gemini. JSON inválido, objetos ambiguos y campos de tipo incorrecto conservan su rechazo genérico. Examinar una pregunta estructuralmente válida con salto de línea antes de su rechazo por controles, para reconocer PEM multilínea sin ampliar el conjunto de preguntas admitidas. Alinear el control de transporte del Worker para que permita al origen clasificar ese caso. Mantener el límite de longitud y los demás controles del cuerpo.
- Responder HTTP `422`, `Cache-Control: no-store`, `X-MacroPulse-Research-Complete: true` y JSON con `code: research_sensitive_information` y `detail: Can't process a question containing sensitive information.`. La marca de finalización indica que el origen consumió la operación y permite liberar el lease. El Worker publica un JSON propio con el código permitido y el detalle fijo. Los demás 422 conservan su tratamiento. El cliente asocia el código solo al 422 y muestra ese texto exacto.
- El Worker ya bloquea la credencial administrativa literal durante la validación del cuerpo. Conservar esa protección local y devolver el mismo 422 con código fijo después de la autenticación administrativa, sin despachar esa credencial, reservar cuota ni simular una finalización de origen. No propagar cabeceras de atestación, consumo o finalización al navegador. Si el código viene del origen, aceptarlo solo para un 422 finalizado y leer un JSON de tamaño acotado. Nunca reenviar el cuerpo de error del origen.
- Aplicar el mismo rechazo previo al runner en el endpoint local de Research v2 habilitado. Mantener la ruta v1 fuera del cambio. La vía administrativa del Worker usa el mismo control de origen que la pública, con sus credenciales, cuota y lease propios.

## Egreso a Gemini

| Etapa | Datos dinámicos permitidos | Datos que quedan en el servidor |
| --- | --- | --- |
| Interpretación | Pregunta, catálogo de indicadores con definiciones, unidades y dimensiones necesarias, identificadores de eventos, filtros explícitos y fecha de corte cuando exista | Configuración, credenciales, versión de transporte, cabeceras y trazas |
| Composición RAG | Proyección explícita de `statement_id`, `category` y texto de afirmación autorizada cuando se necesite para ordenar el relato | Filas, documentos, citas, URL, IDs de fuentes, metadatos editoriales e internos. El servidor conserva la evidencia completa y valida inventario, orden, estilos y correspondencia con las citas |
| Pure LLM Answer | Pregunta original, fechas y filtros relevantes, evento interpretado, corte temporal y código de diagnóstico RAG | Evidencia, documentos, resultados o consultas SQL, referencias privadas, trazas y detalles operativos. La generación conceptual libre y su validación de salida se conservan |

- Usar proyecciones positivas de campos. Los prompts y esquemas estáticos se mantienen controlados por el servidor. El encabezado de autenticación Gemini es el único destino autorizado de la clave de proveedor. El cuerpo, los parámetros de consulta y los logs quedan libres de esa clave.
- Validar centralmente los datos dinámicos de cada etapa antes de entregar el payload al transporte, también en la ruta local v2. Inspeccionar valores de texto antes del escape JSON o tras decodificar la proyección. Un payload bloqueado produce un error fijo sin adjuntar datos. Si una afirmación dispara el detector en composición, conservar el render determinista de afirmaciones verificadas y citas existente.
- `PublicGemini._invoke` usa el mismo contenido validado para `countTokens` y `generateContent`. El primero añade el modelo como parte del envoltorio del proveedor. Flash → Flash-Lite y los reintentos de Flash-Lite primario pasan por el mismo control en cada intento. Conservar modelos, presupuestos, límites de cuerpo, plazos y política de retries.

## Logging

Permitir únicamente nombre de evento, componente, ID aleatorio de correlación, etapa fija, duración, resultado, tipo de error seguro y estado HTTP/origen/proveedor. Modelo y conteos de tokens pueden mantenerse donde ya existan en un esquema de telemetría seguro. Los mensajes, argumentos y trazas de excepciones, URL de proveedor con credenciales, preguntas, cuerpos, encabezados, IP y credenciales quedan excluidos. Comprobar los logs capturados tanto en flujos normales como ante rechazo y fallo del proveedor.

## Propiedad e interfaces

| Worktree | Contrato de entrega |
| --- | --- |
| A · `fix/fase-12-s3-sensitive-input` | Detector, admisión de origen compartida por demo y online, y pruebas de rechazo previo al runner. Los cambios mínimos de la ruta local v2 corresponden a A y se coordinan con B |
| B · `fix/fase-12-s3-egress` | Consume el detector de A por la firma fijada arriba. Proyecta datos y verifica el egreso público y local de Research v2. El servicio conserva las estructuras completas para generar la respuesta |
| D · `fix/fase-12-s3-audit` | Consume el 422 de A por su código y la marca de finalización. Posee Worker y cliente, incluido el ajuste acotado de `frontend/worker/research.mjs` para los casos de validación descritos arriba. Audita cuerpos HTTP, logs y la UI |
| E · `fix/fase-12-s3-integration` | Integra los tres cambios y verifica los contratos desde la misma base de Gate 0 |

Los fixtures compartidos referencian únicamente datos sintéticos. Las piezas de credenciales ficticias se concatenan en memoria para que las pruebas ejerzan el formato sin almacenar claves completas en el repositorio.

## Aceptación y límites

- Demostrar 422 exacto, cero SQL y cero solicitudes a Gemini para cada caso sensible. Verificar consumo único, rechazo de replay, finalización y leasing para Research público y administrativo.
- Capturar ambos cuerpos HTTP de Gemini para interpretación, RAG con varias evidencias y Pure. Comprobar listas de campos y ausencia de marcadores de datos internos. Cubrir error, timeout, failover Flash → Flash-Lite y reintentos de Lite primario. Verificar que la pregunta macroeconómica compleja conserva su contenido y que Pure produce una respuesta libre.
- Probar Worker y cliente con errores de origen desconocidos, cabeceras internas y cuerpos con datos sintéticos. La respuesta visible y los logs deben ajustarse al contrato. Registrar por separado PASS, FAIL y SKIP para backend, Worker, cliente, builds, auditorías y PostgreSQL/workerd cuando corresponda.
- Esta detección se limita a patrones de alta confianza y a secretos configurados conocidos. Nombres, direcciones, prosa confidencial, formatos regionales ambiguos, ofuscaciones arbitrarias o secretos nuevos pueden pasar. El tráfico de pruebas es sintético. Las verificaciones de producción quedan fuera del alcance.

Estado: Gate 0 aprobado.
