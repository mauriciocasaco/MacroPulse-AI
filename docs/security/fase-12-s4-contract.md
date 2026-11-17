# Fase 12.S4 — Contrato de admisión

El origen público valida la autenticación, la atestación y el consumo único de 12.S1 antes de clasificar la pregunta. El detector de 12.S3 tiene precedencia. Tras esas comprobaciones, la barrera de intención examina la pregunta completa en una copia normalizada y devuelve `admitted`, `disallowed_control`, `disallowed_code` o `disallowed_exfiltration`. La pregunta admitida sigue intacta al flujo de investigación. La ruta local v2 aplica las mismas comprobaciones de contenido tras sus controles existentes.

| Rechazo | HTTP | Código | Mensaje |
| --- | --- | --- | --- |
| Datos sensibles | 422 | `research_sensitive_information` | Can't process a question containing sensitive information. |
| Intención no permitida | 422 | `research_disallowed_request` | Can't help you with that type of request. |

Ambos rechazos usan `Cache-Control: no-store`. El origen público marca la finalización y el Worker conserva la liberación del lease. El Worker solo traduce códigos reconocidos de una respuesta de origen completada y validada y produce su propio mensaje fijo. El runner, SQL, Gemini (incluido `countTokens`), RAG y Pure quedan detrás de la admisión. El almacenamiento usado para el consumo único sigue siendo parte de la autenticación.

Los patrones combinan verbos y objetos de una operación, roles y estructuras ejecutables en español, inglés y portugués. Las menciones descriptivas y ecuaciones convencionales siguen admitidas. Una instrucción prohibida dentro de una pregunta económica se rechaza junto con la pregunta.

Los fixtures son sintéticos. Los casos 24 y 47 contienen asignaciones reconocidas por el detector de 12.S3. El payload del caso 48 codifica `print('synthetic')`. La barrera detecta la orden de decodificar y ejecutar, sin interpretar el payload.

Los patrones tienen cobertura parcial de paráfrasis, otros idiomas, sustitución de caracteres visualmente parecidos y texto codificado sin una instrucción explícita. Una cita con estructura de ejecución puede coincidir y una ecuación puramente numérica con sintaxis de asignación puede ser ambigua. La admisión solo describe el resultado de esta política y la disponibilidad de evidencia se determina durante la investigación.
