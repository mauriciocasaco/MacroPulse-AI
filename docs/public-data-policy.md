# Política de datos de la demo pública

La demo de esta fase ofrece tres series sintéticas en puntos de índice. Las
series se generan mediante una fórmula reproducible sobre un calendario diario
ficticio, con símbolos `DEMO-1`, `DEMO-2` y `DEMO-3`. No se calibran con
cotizaciones de mercado ni se vinculan a instrumentos reales.

| Tipo y origen | Almacenamiento y uso | Visualización y redistribución |
| --- | --- | --- |
| Precios reales de Yahoo Finance, obtenidos con yfinance | El código del adaptador y los derechos sobre sus datos son independientes. Esta fase no verifica permisos de adquisición automatizada o retención para el portfolio. | Bloqueada para DEMO. La documentación de Yahoo pide que sus datos no se redistribuyan. Abarca series históricas, cotizaciones, cierres ajustados y derivados que reproduzcan datos restringidos. Verificar también los derechos de bolsas y proveedores originales. [Yahoo Finance](https://help.yahoo.com/kb/SLN2310.html) · [yfinance](https://github.com/ranaroussi/yfinance) |
| Registros y metadatos GDELT GKG | Los datasets publicados por GDELT admiten uso sin tarifa bajo sus términos y requieren atribución. Mantener el origen, URL, fecha reportada, precisión y momento de recuperación. | El permiso sobre el dataset de GDELT no extiende derechos al material de terceros enlazado. DEMO excluye tanto el dataset GDELT como sus enlaces. [Términos de GDELT](https://gdeltproject.org/about.html#termsofuse) |
| Documentos, titulares, citas y pasajes del corpus histórico/RAG | El piloto separa metadatos, pasajes y embeddings. Sus indicadores de derechos se aplican a la procedencia y al uso revisado de cada registro. | DEMO excluye el corpus de DEV. Una habilitación futura requiere verificar individualmente retención, visualización, indexación, embeddings y redistribución. La disponibilidad del documento en la web y un permiso de cita no acreditan esos usos. [Corpus histórico](news-historical-corpus.md) · [Investigación de proveedores](news-provider-research.md) |
| Resultados de análisis, mecanismos y referencias | El proveedor simulado de Fase 4.3 compone resultados a partir de precios y evidencia. Para una futura publicación hay que revisar si los resultados contienen pasajes, títulos, datos de mercado o derivados con restricciones. | El análisis está deshabilitado en DEMO. Una habilitación requerirá etiquetar la interpretación simulada, mantener fuentes y fechas, y conservar las advertencias de cobertura. |
| Series, catálogo, unidades y fechas sintéticas | Material original generado por el proyecto para esta demostración, sin entradas de terceros ni datos personales. La fecha de generación se mantiene en `retrieved_at` por compatibilidad con el contrato de la gráfica. | Apto para visualización de la demo del proyecto con etiqueta sintética visible en la PWA y en metadatos API. Antes de publicar, revisar los datos realmente cargados y confirmar que coinciden con el generador y sus etiquetas. [Generador sintético](../supabase/demo/synthetic.sql) |

Los campos del contrato que se conservan por compatibilidad, como
`ohlc_basis`, describen la serie sin ajuste. La convención
`synthetic:marketpulse-demo-v1` y el proveedor identifican su origen ficticio.
Los campos de volumen, ajuste, cierre de sesión de mercado y disponibilidad
histórica real no se inventan. Las fechas del gráfico corresponden al calendario
sintético y el timestamp de generación no implica disponibilidad histórica.

## Control de publicación

1. Revisar proyecto, propietario, huella del generador, conteos y etiquetas.
   Aprobar únicamente las tablas y campos que forman parte de la demo.
2. Excluir backups, exportaciones de DEV, capturas con datos reales, documentos,
   vectores y derivados restringidos. Mantener el esquema dedicado fuera de
   las APIs de datos anónimas.
3. Registrar, para cada futuro dataset, titular, versión de términos, vigencia,
   derechos por campo, restricciones territoriales, atribución, conservación,
   visualización y redistribución. Obtener aprobación explícita si el permiso
   resulta insuficiente o incierto.
4. Verificar la API y la PWA con la lista de datos aprobados. Interrumpir la
   publicación si las etiquetas, el contenido o la autorización no coinciden.
