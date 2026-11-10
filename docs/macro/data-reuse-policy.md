# M1 — Reutilización de estadísticas oficiales

Revisión del 9 de noviembre de 2026. El alcance es almacenar los datos
públicos revisados, reproducir observaciones y mostrarlas en una PWA de
portfolio de acceso libre. La decisión se aplica a cada combinación de
clave y fuente. Un cambio de proveedor, clave, titularidad, alcance de
publicación o condiciones requiere una nueva revisión.

## Condiciones de origen

- **BCE y SEBC.** La política del SEBC permite reutilizar sin cargo sus
  estadísticas públicas con atribución y preservación de los datos y
  metadatos publicados. Excluye datos de terceros sin permiso previo del
  originador y estadísticas confidenciales. El aviso del BCE exige
  reproducción fiel y atribución, identificación explícita de
  modificaciones, divulgación del acceso gratuito antes del cobro y en cada
  acceso cuando se incorpora el contenido a documentos de pago, y enlaces
  sin incrustar su sitio en marcos para los casos indicados. Exige permiso
  previo para republicar ciertos documentos firmados. La regla para esta
  PWA es preservar las estadísticas BCE/SEBC y usar una presentación que
  mantenga valor, significado y metadatos. Las modificaciones numéricas
  para redifusión requieren una revisión separada.
  [Política estadística del SEBC](https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html) ·
  [Aviso y derechos del BCE](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html).
- **Eurostat.** El aviso autoriza reutilización comercial y no comercial de
  estadísticas y metadatos con reconocimiento de la fuente, sujeto a los
  avisos individuales y excepciones. Para datos modificados se declara el
  cambio y se añade una nota de no responsabilidad de Eurostat. Material
  identificado con derechos de terceros requiere examinar al titular y
  sus condiciones. El aviso distingue datos de otras fuentes que sólo
  admite para reutilización no comercial, y limita logos y marcas. La
  autorización estadística es distinta de CC BY 4.0 para contenido editorial.
  Para un dataset se cita su DOI y fecha de acceso, o enlace al código y
  fecha si se cita una selección personalizada.
  [Aviso de derechos y reutilización de Eurostat](https://ec.europa.eu/eurostat/help/copyright-notice) ·
  [Decisión 2011/833/UE](https://eur-lex.europa.eu/eli/dec/2011/833/oj).

## Decisión por serie y canal

| Clave obtenida en el BCE | Condición para almacenar, reproducir y mostrar | Atribución mínima en la PWA |
| --- | --- | --- |
| `EXR.D.USD.EUR.SP00.A` | Permitido bajo política BCE/SEBC para esta referencia pública, conservando observación, unidad y metadatos. Uso informativo. El BCE desaconseja el uso transaccional. | «Fuente: estadísticas del BCE, tipo de cambio de referencia EUR/USD». |
| `MNA.Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.G1` | El BCE calcula y publica el G1, a partir de volúmenes de Eurostat. La política del SEBC deja fuera los datos de terceros sin permiso de origen. El permiso estadístico de Eurostat cubre su dataset. Para esta transformación y canal del BCE, se bloquean la ingesta persistente para redifusión pública y la publicación hasta confirmar los derechos con ambos orígenes. La ruta directa de Eurostat está habilitada por separado bajo su aviso. | Si se habilita la ruta BCE, «Fuente: BCE, cálculo a partir de cuentas nacionales de Eurostat». Para la alternativa se cita Eurostat con dataset y fecha de acceso. |
| `HICP.M.U2.N.000000.4D0.ANR` | El BCE redistribuye un agregado de Eurostat (`4D0`). Eurostat autoriza sus estadísticas bajo su aviso. La política del SEBC no es una autorización independiente para esta clave. La ruta pública aprobada por alcance es Eurostat directo. La ruta BCE queda bloqueada para ingesta persistente destinada a redifusión y publicación hasta verificar el permiso aplicable al canal. | «Fuente: Eurostat, prc_hicp_minr» para la ruta directa. Si se habilita la ruta BCE, «Fuente: Eurostat, distribuido por el BCE». |
| `BSI.M.U2.Y.V.M30.X.1.U2.2300.Z01.E` | Permitido bajo política BCE/SEBC para el saldo público del SEBC. La gráfica mantiene escala, unidades, ajuste, composición y estatus. | «Fuente: estadísticas del BCE/SEBC, BSI, saldo de M3». |
| `FM.B.U2.EUR.4F.KR.DFR.LEV` | Permitido bajo política BCE/SEBC para la tasa oficial. Mostrar eventos con vigencia efectiva. | «Fuente: Banco Central Europeo, facilidad de depósito». |

La atribución en gráficos, tablas y descargas incluye enlace a la
[ficha oficial de cada serie](official-series-contracts.md), clave o
selección exacta, fecha de captura, unidad y notas de presentación.
El portfolio no usa logos ni sugiere respaldo institucional. Los
metadatos de origen y las convenciones locales de presentación se
identifican por separado. Los datos de otros proveedores requieren
su propia verificación. Un resultado HTTP público no autoriza su
redifusión.

## Alternativas directas de Eurostat

- **PIB.** [Dataset namq_10_gdp, DOI 10.2908/NAMQ_10_GDP](https://doi.org/10.2908/NAMQ_10_GDP),
  seleccionar `freq=Q`, `unit=CLV_PCH_PRE`, `s_adj=SCA`,
  `na_item=B1GQ`, `geo=EA21`. La API devuelve 0,6 % para 2026-Q2.
  Equivalencia económica de la muestra a la precisión de Eurostat.
- **IAPC.** [Dataset prc_hicp_minr](https://ec.europa.eu/eurostat/databrowser/product/view/prc_hicp_minr),
  seleccionar `freq=M`, `unit=RCH_A`, `coicop18=TOTAL`, `geo=EA`
  (composición cambiante). La API devuelve 2,8 % para junio de 2026.
  La [información metodológica](https://ec.europa.eu/eurostat/web/hicp/information-data)
  explica la revisión de clasificación y las correcciones, que deben
  considerarse al verificar historia más larga.

```powershell
$eurostat = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'
curl.exe -fsiS --max-time 45 "$eurostat/namq_10_gdp?freq=Q&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ&geo=EA21&time=2026-Q2"
curl.exe -fsiS --max-time 45 "$eurostat/prc_hicp_minr?lang=en&freq=M&unit=RCH_A&coicop18=TOTAL&geo=EA&time=2026-06"
```

Las fuentes directas se almacenan bajo identidades independientes, con su
propia precisión, flags y fechas. La coincidencia de la muestra no certifica
todas las versiones, disponibilidad histórica ni equivalencia a través de
cambios de clasificación. La verificación de muestras no autoriza tareas
operativas en esta fase.

## Corpus institucional de M5

El [corpus local](../../backend/macro_ai/corpus.json) registra ocho
publicaciones fechadas del BCE y Eurostat. Cada entrada conserva título,
URL, publicación, atribución y aviso de reutilización. Guarda un
resumen elaborado por MacroPulse AI con hechos de la publicación
oficial. Se revisaron la procedencia y los avisos de cada fuente.

- Para las publicaciones institucionales del BCE se aplica su aviso
  de reproducción fiel y atribución. Los resúmenes indican que son
  adaptaciones. El comunicado conjunto sobre liquidez en USD aporta
  únicamente hechos resumidos. El corpus excluye textos firmados,
  material gráfico y reproducciones de aportes de terceros.
- Para los dos comunicados de Eurostat se aplica su aviso de
  reutilización. La atribución identifica a Eurostat, la adaptación
  y la ausencia de responsabilidad por el resumen. Las cifras de una
  publicación histórica se mantienen separadas de las series actuales.

Se permite guardar, fragmentar, indexar y procesar estos resúmenes
atribuidos, así como mostrar citas y fragmentos del propio resumen.
Los embeddings se calculan localmente. Si se habilita Gemini en
loopback, el contexto enviado contiene el tema reconocido, las fechas,
los resúmenes seleccionados y hechos o cálculos sobre las cinco series
aprobadas. La pregunta original se procesa localmente.
La aplicación pública sirve referencias y gráficos según sus propios
controles de M4. La generación de respuestas permanece local.
