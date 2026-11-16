# Fase 12.S2 · Contrato de seguridad

Base revisada después de `git fetch origin`: `b6e6abde261ae14d29085aae6dd63e9003e8f6a2`. Incluye `ad303c37` (PR #34). El cliente público usa Research v2. Demo y Online comparten la ruta FastAPI. El cliente Gemini en esta base conserva el salto Flash → Flash-Lite y también admite hasta tres intentos breves al configurar Flash-Lite como primario.

## Gate A

- **Origen.** La atestación autentica método, ruta, cuerpo, lease y scope. El callback consume la reserva antes de entrar a FastAPI Research. La admisión se realiza después del consumo y del parseo acotado, antes del runner, SQL o cualquier petición externa a Gemini, incluido `countTokens`. Una reserva consumida permite evaluar la pregunta una vez.
- **Worker.** Un rechazo semántico devuelve HTTP 422 con `{"code":"research_question_not_admitted"}`, `no-store` y el marcador interno de finalización. El Worker libera la concurrencia según el protocolo vigente y reconstruye el código fijo únicamente a partir de una respuesta de origen completada y validada. Conserva su 422 técnico genérico. No devuelve la entrada ni diagnósticos internos.
- **Capacidad.** Preguntas descriptivas con vocabulario y forma macroeconómicos acotados mantienen el recorrido RAG v2. Las conceptuales usan coincidencia de pregunta completa con plantillas y temas aprobados. Controles del agente, entradas reconocidas como sensibles, caracteres fuera del alfabeto aceptado y cláusulas no representables se rechazan incluso si incluyen una palabra macroeconómica. Las categorías internas son heurísticas informativas. La barrera se aplica a la solicitud completa y no depende de acertar la etiqueta del ataque.
- **Elegibilidad.** La pregunta conceptual exacta activa una evaluación del servidor sobre el alcance del RAG. Las fuentes actuales aportan observaciones y evidencia de eventos, y el sistema carece de un índice de definiciones y mecanismos conceptuales. Esa carencia específica es `unsupported_operation_or_series`, HTTP 200 con RAG insuficiente. El camino descriptivo conserva sus diagnósticos y nunca habilita el selector conceptual. Manipular el Intent, las unidades, la geografía, las fechas o el diagnóstico no puede cruzar esa frontera. Los errores y resultados parciales se conservan. El flag de fallback y el presupuesto del cliente Gemini siguen siendo obligatorios.
- **Producto.** La pestaña sigue disponible en el mismo resultado y pasa a llamarse «Conceptual Explanation». Gemini selecciona fragmentos revisados del catálogo. El servidor redacta la representación pública a partir de ese catálogo versionado, sin interpolar valores. La explicación sigue marcada como no verificada para la pregunta histórica. El catálogo cubre inflación, política monetaria, tipo de cambio, facilidad de depósito, dinero amplio y PIB, con dos relaciones económicas explícitas. El catálogo tiene prosa en inglés. El reconocedor conceptual incluye algunas formas en español.
- **Límites.** Los falsos rechazos incluyen sinónimos y preguntas legítimas sin plantilla, además de comparaciones fuera del vocabulario y escrituras no cubiertas. Las fechas de la interfaz no convierten una definición en un resultado histórico. El catálogo describe mecanismos posibles y definiciones generales, sin cifras, citas, hallazgos institucionales ni atribuciones a eventos. La moderación semántica general y la detección exhaustiva de datos confidenciales quedan fuera de esta capacidad.

## Transiciones y contrato entre ramas

| Entrada o estado | Autoridad | Resultado |
| --- | --- | --- |
| HMAC, scope, reserva o callback inválido | 12.S1 | Denegación existente |
| Error de transporte, JSON o esquema | FastAPI o Worker | Error técnico existente |
| Ambigua, fuera de dominio, sensible, control o forma no admitida | Admisión determinista | 422 genérico, sin SQL ni Gemini |
| Descriptiva | Admisión determinista + RAG | RAG válido, parcial, insuficiente o error |
| Conceptual representable y RAG sin cobertura | Evaluación del alcance en servidor | RAG insuficiente, selector condicionado a flag y presupuesto |
| Selección válida | Esquema + subconjunto autorizado | 200 con RAG original y explicación |
| Fallo de proveedor, esquema, ámbito u orden | Cliente Gemini + contrato | 200 con RAG original y `status: failed` |

A es propietario de este contrato, del catálogo `backend/macro_ai/conceptual_catalog.json` y de los fixtures. B es propietario de `question_admission.py`, Demo y Online. `classify_question(question)` devuelve una decisión congelada con `kind`, `admitted` y `fragment_ids`. B también conecta la consulta de presupuesto del proveedor en los perfiles públicos. C posee el cliente Gemini, los modelos y el grafo. D posee Worker y cliente. E integra commits y verifica contra los fixtures.

El proveedor recibe exclusivamente los IDs y los fragmentos autorizados para la pregunta, en orden de catálogo. Devuelve `{"fragment_ids":[...]}`. Se exige un prefijo ordenado del inventario autorizado, con uno o dos IDs y ancla obligatoria. Campos adicionales, duplicados y prosa del proveedor se rechazan. El wire de éxito conserva `answer`, `verified: false`, `status`, `triggered`, `reason` y `model_id` y añade `catalog_version: "v1"` y `fragment_ids`. `answer` es exactamente los fragmentos unidos por dos saltos de línea. El cliente verifica la representación exacta contra el mismo catálogo y suprime un fallback inválido conservando RAG. Cada versión publicada es inmutable.

El presupuesto conserva el máximo actual de generaciones, el preflight y los topes por modelo. El selector es una etapa opcional y realiza como máximo un preflight y una generación. Puede omitirse por falta de tiempo. No consume una conexión de datos.

## Amenazas verificables

| Amenaza | Antes | Criterio de aceptación |
| --- | --- | --- |
| Inyección directa, mensaje mixto, Unicode, credenciales | La pregunta llegaba al preflight del intérprete | Cero llamadas SQL, generación y preflight en la ruta atestada |
| Diagnóstico provocado mediante Intent | Diagnóstico de insuficiencia habilitaba el generador | Un Intent nunca abre la rama conceptual |
| Cifras en palabras, atribución y causalidad confirmada | Prosa comprobada por patrones | Solo se renderiza contenido del inventario local de la pregunta |
| ID inventado, cruzado o duplicado, respuesta truncada | Sin inventario de fragmentos | RAG intacto y fallo genérico en el selector |
| 422 con cuerpo hostil, HTML activo o enlace | Worker sustituía todo 422 por un error técnico | Código fijo acotado, RAG preservado, catálogo validado en el cliente |
| Reutilización de reserva y credencial administrativa | Protecciones 12.S1 | Misma autenticación, consumo, release y scopes |

Fixtures sintéticos compartidos en `tests/fixtures/security/fase-12-s2.json`. Los resultados observados y los límites de verificación se registran en el documento de integración.
