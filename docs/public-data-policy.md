# Política de datos de la demo pública de MacroPulse AI

| Datos | Alcance autorizado de la demo |
| --- | --- |
| BCE y SEBC | Referencia EUR/USD `EXR.D.USD.EUR.SP00.A`, saldo M3 `BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E` y facilidad de depósito `FM.B.U2.EUR.4F.KR.DFR.LEV`. Se conservan unidad, escala, ajuste, período, vigencia, fecha de captura y atribución. |
| Eurostat | PIB real trimestral `namq_10_gdp` y variación anual HICP `prc_hicp_minr` vía Eurostat directo, con las selecciones de dimensiones fijadas en el catálogo. |
| Corpus M7.B | Resúmenes atribuidos y afirmaciones estructuradas aprobadas. Research devuelve referencias, fechas, localizadores, calificaciones y aviso de reutilización. Mantiene el corte documental y distingue observación, cálculo, interpretación y proxy. |
| Preguntas de visitantes | El envío requiere consentimiento en la PWA. Gemini recibe la pregunta original y, para la composición, las frases verificadas y sus fuentes. La aplicación no guarda preguntas ni respuestas. Las cuotas guardan un HMAC de la IP durante el día UTC y la ventana móvil que cruce medianoche, y el hash del token hasta su expiración. |

Las condiciones y excepciones por serie se detallan en la
[política de reutilización estadística](macro/data-reuse-policy.md) y el
[contrato de fuentes oficiales](macro/official-series-contracts.md).
La revisión editorial de los 24 documentos y 47 afirmaciones figura en el
[inventario M7.B](m7b/README.md). La demo excluye observaciones
confidenciales, material original de terceros, fuentes de mercado,
noticias GDELT, corpus de DEV y embeddings. La migración sintética de Fase
4.4 queda como referencia histórica y no forma parte del despliegue actual.

La carga administrativa valida HTTPS de origen en la captura, claves exactas,
dimensiones, unidades, cobertura, fechas y huellas. La proyección distingue
el período económico de la fecha de captura. Los cálculos históricos usan
las versiones de la captura DEMO. La aplicación declara esa limitación en
Research y restringe la evidencia documental a su fecha de publicación.
Una revisión de fuente, permiso, indicador, representación o alcance exige
volver a comprobar el derecho de publicación.

El proyecto Gemini debe usar una cuenta dedicada con la configuración de
privacidad y uso de datos aprobada para una demo pública. Informar sobre el
envío antes de la solicitud y comprobar los términos y la retención del plan
en la fase de activación. Las plataformas pueden aplicar su propia
telemetría o retención. Configurar logs y monitoreo para que los datos
identificables y las credenciales queden fuera de la evidencia operativa.
[Condiciones adicionales de Gemini API](https://ai.google.dev/gemini-api/terms).
