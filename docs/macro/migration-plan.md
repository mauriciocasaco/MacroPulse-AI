# MacroPulse AI — Plan de migración aditiva

La línea de base es `baseline/phase-4.4a` en
`06fcad69370fe60de9121e9e19554819b9282a00`. Los contratos macro tienen un
ciclo separado del catálogo de activos, precios y noticias de MarketPulse
AI. La fase M1 documenta la incorporación futura y conserva el
comportamiento existente.

| Hito | Alcance y condición de salida |
| --- | --- |
| M0 | Línea de base y evaluación inicial de fuentes oficiales. |
| M1 | Verificar claves, significado, escala, estados, fechas y derechos. Definir contrato temporal y diseño de persistencia. |
| M2 | Implementar ingesta independiente con validación, capturas trazables, idempotencia, pruebas sintéticas y reglas de publicación por fuente. Revisar el esquema y cualquier autorización operativa antes de aplicar cambios de infraestructura. |
| M3 | Exponer una lectura macro con períodos, revisiones, evidencia, vigencia y política de derechos. Validar compatibilidad de los consumidores actuales y separación de permisos. |
| M4 | Integrar las visualizaciones macro en la PWA tras habilitar derechos por serie y verificar atribución, unidades y manejo de huecos. |
| M5 | Implementar RAG verificable con LangChain, orquestación LangGraph y Gemini API. Fijar dependencias y modelo al implementar. Validar recuperación con fuentes autorizadas y fechas de disponibilidad, citas trazables a cada fuente y versión, abstención ante falta de evidencia y tratamiento de contenido recuperado como datos. Mantener fixtures locales para pruebas deterministas y una comprobación real separada con autorización y presupuesto explícitos. |

## Contrato temporal

Cada serie tiene una identidad estable de fuente, flujo y clave, además
de un contrato versionado para unidad, escala, frecuencia, convención de
período, ajuste, composición, definiciones y política de reutilización.

| Campo de observación | Semántica |
| --- | --- |
| `period_raw` | Texto exacto de `TIME_PERIOD`; parte de la clave lógica junto a la serie. |
| `period_start`, `period_end` | Fechas calendario inclusivas. `2026-06-30` diario empieza y termina ese día; `2026-06` cubre del 1 al 30 de junio y `2026-Q2` del 1 de abril al 30 de junio. Para eventos, ambas fechas son la fecha efectiva de la observación. Un saldo mensual mide el cierre del período. |
| `value_raw`, `value_decimal` | Lexema de origen y Decimal de precisión exacta, serializado como cadena al intercambiarlo. `null` para un valor ausente declarado. En M3, conservar el valor en millones y su multiplicador 6; una proyección opcional a EUR usa Decimal y escala 0. En PIB, el Decimal leído es un porcentaje aunque la metadata bruta indique EUR y 1. |
| `status_raw`, `status_kind`, `confidentiality_raw` | Preservar el código de proveedor y mapear según la versión de su lista de códigos. Distinguir normal, provisional, estimado, definitivo si el proveedor lo declara, ausente y desconocido. Registrar revisiones independientemente del estado. Mantener la confidencialidad en un campo separado y excluir de publicación estados no permitidos o sin interpretar. |
| `captured_at_utc` | Fecha y hora UTC de la captura local de la respuesta. Prueba lo que conocía el sistema a esa hora, sin retrotraer conocimiento al período observado. |
| `provider_vintage_id`, `provider_valid_from`, `provider_valid_to` | Identificador y límites de versión en origen, opcionales. `validFromDate` de SDMX es evidencia de vigencia en el repositorio de origen. Su semántica debe conservarse con la evidencia. |
| `published_at`, `publication_evidence` | Hora de publicación de esa versión, cuando hay evidencia oficial que vincula serie, período, estado y valor. Si sólo consta una fecha, guardar la fecha con su precisión y huso declarados cuando corresponda, sin fabricar hora. Si no se puede vincular una versión, mantener `null`. |
| `effective_date` | Fecha efectiva del evento de política. Independiente de la fecha del anuncio, de la versión y de la captura. |
| `revision_id`, `supersedes_revision_id` | Identidad local de versión de la observación y vínculo a la revisión anterior cuando el orden se conoce. Una revisión registra cambios de valor, estado u otros atributos relevantes, o una nueva versión explícita del proveedor. Una retirada requiere una señal oficial y queda como versión de baja. |

El modo **tal como se conocía localmente** elige sólo revisiones
capturadas hasta el instante de corte. El modo **publicación histórica
verificada** admite versiones con evidencia suficiente para el corte
solicitado. Devuelve «disponibilidad no verificada» cuando el dato no se
puede fechar con la precisión requerida. Se conserva la evidencia a nivel
de dato y versión. El calendario de publicación, la última actualización
del portal, la fecha de vigencia de toda la serie, el fin del período y
la hora de una descarga posterior no prueban cuándo se publicó una
revisión concreta.

La [API SDMX del BCE](https://data.ecb.europa.eu/help/api/data) documenta
`updatedAfter` para obtener cambios y `includeHistory` para pedir
versiones previas. Validar disponibilidad y semántica por flujo. Dos
respuestas XML HTTP 200 de M1 devolvieron una versión en cada muestra
(`action=Replace`), sin demostrar que hubo cambio de valor, cuándo se
publicó por primera vez ni que exista historial completo:

| Consulta acotada | `validFromDate` del DataSet | Observación |
| --- | --- | --- |
| MNA G1, 2026-Q2 | `2026-10-20T11:16:18.000+02:00` | 0.6449960748375227 |
| HICP, 2026-06 | `2026-07-17T11:32:29.000+02:00` | 2.8 |

```powershell
$ecbApi = 'https://data-api.ecb.europa.eu/service/data'
curl.exe -fsiS --max-time 45 "$ecbApi/MNA/Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.G1?startPeriod=2026-Q2&endPeriod=2026-Q2&includeHistory=true&format=genericdata"
curl.exe -fsiS --max-time 45 "$ecbApi/HICP/M.U2.N.000000.4D0.ANR?startPeriod=2026-06&endPeriod=2026-06&includeHistory=true&format=genericdata"
```

### Ausencias y visualización

Conservar ausencias declaradas por la fuente y distinguirlas de un
período esperado que falta en una captura completa, un cierre de TARGET,
una ventana de eventos sin cambios y un fallo de descarga o cobertura.
Una ausencia no es cero ni retirada automática. Para las series mensuales
y trimestrales se visualizan puntos por período y huecos visibles, sin
interpolación. El dato diario se presenta a su frecuencia original.
Todos los indicadores son observaciones macro, sin conversión a OHLCV.

Para la tasa de depósito, los datos observados son los eventos. Una
vista escalonada representa el intervalo `[fecha_efectiva,
siguiente_fecha_efectiva)` con evidencia de cobertura. Puede incluir un
evento previo como ancla si se conoce. Sin ancla o fuera del intervalo
cubierto, mostrar desconocido. La vigencia derivada no se materializa como
filas observadas y nunca se adelanta a la entrada en vigor. Son
presentaciones permitidas el formato de etiquetas, el redondeo visual
explícito y la representación en el eje temporal manteniendo accesibles
los datos originales. Las transformaciones analíticas se separan,
documentan y pasan por la [política de reutilización](data-reuse-policy.md).

## Diseño preliminar de macro_data

Diseño lógico para evaluar en M2, sin DDL.

| Entidad | Campos principales e invariantes |
| --- | --- |
| `series` | Id interno y unicidad por (fuente, flujo, clave). Contratos y políticas versionados, con fecha, vigencia, URLs de metodología y derechos, y decisión de almacenamiento y publicación por ruta. |
| `capture` | Id de lote, URL sin credenciales, parámetros, hora UTC, resultado HTTP, formato, huella SHA-256 de respuesta, versión del contrato y cobertura. Referencia a los bytes cuando la política permite persistirlos. |
| `observation_revision` | Observación del contrato temporal y referencias a contrato y captura. Identidad lógica (serie, período SDMX). Revisiones inmutables ordenadas por evidencia de proveedor o, para conocimiento local, por captura. Índices previstos por (serie, período, captura), (serie, período, fecha de versión) y (serie, fecha efectiva). |
| `revision_capture` | Vínculo entre revisiones y capturas repetidas, con unicidad del par. Conserva el primer instante conocido y evidencia de reobservación sin duplicar la revisión. |
| `sync_checkpoint` | Cursor por serie y formato, último éxito completo y cobertura. El cursor avanza junto con el lote válido en una sola transacción. |

Validar clave exacta, dimensión y unidad por contrato, formato, rango
temporal, escala, flags y derechos antes de promover una captura.
Agrupar reintentos equivalentes por identificación oficial de versión o
reconciliación de la revisión activa con su huella normalizada. Un valor
que retorna después de una revisión intermedia (`A → B → A`) es otra
revisión, aunque el contenido coincida. Conservar versiones explícitas
distintas aunque compartan valor. Serializar la reconciliación por serie
y período, con identificadores únicos para que reintentos concurrentes
no dupliquen filas. Una descarga fallida, parcial o fuera de orden no
borra datos ni sustituye una versión posterior. Tratar correcciones de
metadatos y cambios de proveedor como nuevos contratos o series según
cambie el significado.

En M2, las credenciales de ingesta quedan del lado servidor. Proyectar
lectura pública sólo desde una lista explícita de contratos, revisiones y
atributos habilitados. Denegar escrituras públicas y separar
configuraciones, análisis privados y cualquier dato por usuario del
catálogo público. Probar permisos en entornos locales o dedicados antes
de exponer lecturas. Mantener aisladas la persistencia y las rutas de
consulta de MarketPulse AI.

## Verificaciones de salida para M2

- Usar fixtures sintéticos identificados como tales, representativos de
  los cinco contratos. Cubrir orientación del cambio, multiplicador de
  M3, unidad bruta versus canónica del PIB y fecha efectiva de política.
- Cubrir fin de mes bisiesto y trimestre, TARGET cerrado, estado
  provisional, revisión con y sin cambio de estado, huecos, bajas
  oficiales, reintento, fallo parcial, concurrencia y capturas en
  desorden. Probar un corte temporal sin disponibilidad verificable.
- Confirmar compatibilidad con MarketPulse AI, ausencia de datos
  privados y preservación de la política de reutilización por clave y
  origen. Confirmar fuentes y rutas del PIB e IAPC antes de publicarlos.
