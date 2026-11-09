# Piloto local de corpus histórico

El piloto usa las ventanas originales de COVID-19, invasión rusa de Ucrania e
IA generativa. Descubre candidatos en los índices de EIA y de la Junta de la
Reserva Federal, con una semilla documentada para el análisis de WTI negativo.
El adaptador histórico GKG 2.1 inspecciona un archivo de 15 minutos por evento
y conserva hasta ocho coincidencias por ruta del enlace. La API DOC tiene una
ventana móvil, por lo que el piloto utiliza los archivos para estas fechas.
[GDELT DOC](https://blog.gdeltproject.org/doc-2-0-api-llms-summarizing-headlines-turkish-investment-inflation-the-niger-coup/)
· [Datos GDELT](https://www.gdeltproject.org/data.html)

## Alcance de los permisos

| Fuente | Material local | Reutilización |
| --- | --- | --- |
| EIA, Today in Energy | Prosa institucional normalizada para almacenamiento, fragmentación y embeddings | Atribuir a EIA. Excluir imágenes, tablas, créditos y material marcado como protegido. [Política de la serie](https://www.eia.gov/TODAYINENERGY/about.php) |
| Junta de la Reserva Federal | Prosa de discursos de autoridades y secciones de informes de la Junta para almacenamiento, fragmentación y embeddings | Atribuir a la Junta. Excluir gráficos, tablas, pies y materiales identificados como de terceros. [Política de la Junta](https://www.federalreserve.gov/disclaimer.htm) |
| GDELT GKG 2.1 | Metadatos y enlaces, con ID de registro, fecha de observación y hash del archivo | Atribuir a GDELT. La publicación reportada por GKG queda sin verificar. Los cuerpos de las publicaciones y sus derechos se mantienen fuera del corpus. [Términos](https://www.gdeltproject.org/about.html) |

La autorización del piloto excluye servicios de inferencia externos. El
filtro de derechos usa series permitidas y excluye señales de derechos de
terceros. Una selección nueva de series requiere revisar sus políticas y
sus estructuras de extracción antes de habilitarla.

## Reproducción

Ejecutar desde la raíz con Python 3.12 y las dependencias de desarrollo.
La adquisición importa Requests y Beautiful Soup. Para la evaluación, usar
el entorno de inferencia y el caché E5 fijado que describe el
[piloto de recuperación](news-retrieval-pilot.md#phase-35--embedding-selection).

```powershell
python -m backend.news.historical_corpus

docker run --rm -d --name marketpulse-corpus --publish 127.0.0.1::5432 -e POSTGRES_HOST_AUTH_METHOD=trust pgvector/pgvector:0.8.2-pg17
try {
    $corpusPort = (docker port marketpulse-corpus 5432/tcp) -replace '^127\.0\.0\.1:', ''
    python -m backend.news.evaluate_historical_corpus --port $corpusPort --model-cache $modelCache
} finally {
    docker stop marketpulse-corpus
}
```

Usar un contenedor desechable y esperar a que acepte conexiones antes de
evaluar. El comando conecta a la dirección literal 127.0.0.1, crea su propia
base de datos temporal, aplica las migraciones locales y la elimina al
finalizar. Los vectores se generan por CPU con los pesos locales. Un cierre
forzado puede dejar la base temporal hasta que se elimine el contenedor.

Los resultados quedan en `data/historical-corpus/`, ignorado por Git.
`manifest.json` registra descubrimiento, derechos, procedencia y hashes.
`evaluation.json` contiene las preguntas, juicios, controles, rankings y
métricas. `revision-humana.md` contiene la matriz y los documentos para
inspección. La adquisición conserva los textos normalizados por URL con
su fecha de descarga original y verifica su hash al reanudar. Los
índices y las muestras GKG se vuelven a consultar al repetir la adquisición.

La evaluación usa `publication_day` con ventanas UTC de días completos y
conserva la precisión original. Es retrospectiva. Las versiones hoy
disponibles pueden incorporar correcciones posteriores y la fecha por día
no acredita disponibilidad intradía. Las consultas `as_of` siguen
excluyendo registros sin hora de publicación y observación verificables.

El benchmark compara ranking léxico con ranking híbrido sobre las mismas
preguntas en español y textos en inglés. Usa un conjunto pequeño de
referencias positivas y controles seleccionados, con resultados no
juzgados en el ranking. Los filtros por activo pueden excluir controles
antes de puntuar. El diseño mide este flujo y este corpus, con sesgo hacia
energía de Estados Unidos y contexto de política monetaria. No estima
cobertura de prensa general ni identifica efectos causales en los activos.

## Carga revisada en Supabase DEV

La carga del 9 de noviembre de 2026 se vinculó al SHA-256 del manifiesto
`a24e3341caa7f4c30ed10fdd4b0f7cef2bda664ad044f5a687c599e4ba11acd9`.
Cada texto se comparó con su registro local, hash, origen, fecha, ámbito de
extracción y política. Las políticas de EIA y de la Junta permiten copiar y
distribuir su prosa de dominio público con atribución, salvo indicación
contraria. Esa base permite almacenarla en DEV y procesarla para embeddings
locales. Se excluyeron elementos gráficos, tablas y materiales identificados
como de terceros durante la extracción. Los 20 registros GKG se cotejaron con
los hashes y las filas de tres archivos del proveedor. El importador
conserva la atribución y el enlace de GDELT, y deja las fechas de publicación
no verificadas como texto del proveedor. [EIA](https://www.eia.gov/TODAYINENERGY/about.php) ·
[Junta de la Reserva Federal](https://www.federalreserve.gov/disclaimer.htm) ·
[GDELT](https://www.gdeltproject.org/about.html)

```powershell
$reviewedManifest = 'a24e3341caa7f4c30ed10fdd4b0f7cef2bda664ad044f5a687c599e4ba11acd9'
python -m backend.news.dev_corpus --dry-run --reviewed-manifest-sha256 $reviewedManifest --model-cache $modelCache
python -m backend.news.dev_corpus --verify-dev --reviewed-manifest-sha256 $reviewedManifest --model-cache $modelCache
python -m backend.news.evaluate_historical_corpus --dev-read-only --model-cache $modelCache
```

El modo de escritura usa `--apply-dev --confirm-dev-write swpithkjviavycrxpwed`
en lugar de `--verify-dev`. La validación de un manifiesto nuevo requiere
revisar los derechos y la procedencia de cada elemento antes de usar su
hash. El dry-run calcula los vectores offline. Los modos DEV usan la
conexión verificada por el importador de migraciones, preflight de ledger y
un cotejo de filas y vectores. La aplicación usa una sola transacción y
bloqueos limitados a las tablas de noticias. La reingesta del mismo paquete
verificó 51 documentos con texto, 20 solo metadatos, 299 embeddings y 424
asociaciones de registro con activos, con cero filas nuevas. El tag
`marketpulse:event:<evento>` identifica el escenario de curaduría. Se
descartan las etiquetas de proveedor con prefijo reservado `marketpulse:`.
`--verify-dev` valida el paquete esperado y devuelve código distinto de
cero si hay filas faltantes o discrepancias. El inventario global de
noticias se verifica por separado con consultas de solo lectura.

La evaluación DEV escribe `dev-evaluation.json` y `dev-revision-humana.md`
en la carpeta local del corpus. Las referencias son juicios manuales
parciales. La precisión mínima divide los aciertos etiquetados por tres
cuando K es tres. Los controles negativos recuperados y los resultados
sin juicio se distinguen en el JSON. Las barras se leen en una
transacción de solo lectura y representan cierres históricos de la versión
actual del proveedor. Se calculan por separado de las explicaciones
económicas. En WTI durante COVID-19 el cierre mínimo fue −37,63 USD/bbl
el 20 de abril de 2020 y se omite la variación porcentual del período.

De los 18 escenarios, dos tuvieron evidencia directa, ocho contextual y
ocho insuficiente según el juicio documental. En los diez con referencias
positivas, el híbrido obtuvo Hit@3 de 1,00 y Recall@3 de 0,90. El léxico
obtuvo 0,80 y 0,65. En los primeros tres puestos de los 18 escenarios
aparecieron siete controles negativos con el híbrido y dos con el
léxico, además de 15 y 10 resultados sin juicio, respectivamente. Para
revisión manual, contrastar el mecanismo de demanda, almacenamiento y
vencimiento del WTI durante COVID-19 con la coincidencia entre demanda
eléctrica de centros de datos y WTI en el escenario de IA. La coincidencia
léxica o semántica de este último caso aporta un falso positivo conocido.
[EIA, WTI negativo](https://www.eia.gov/todayinenergy/detail.php?id=43495) ·
[EIA, demanda eléctrica](https://www.eia.gov/todayinenergy/detail.php?id=64129)

### Fuentes para ampliar los juicios

- El Banco Mundial analiza la demanda de refugio y los flujos hacia ETF
  de oro en el primer trimestre de 2022. La sección de metales preciosos
  del informe publicado en abril aporta contexto para `GC=F`. Su licencia
  CC BY 3.0 IGO exige atribución y revisión de material de terceros. El
  análisis se refiere al oro y a los ETF, con una base distinta del
  contrato continuo de futuros. Quedó como candidato fuera del índice y
  de las métricas de esta ejecución. [Commodity Markets Outlook, abril de 2022](https://thedocs.worldbank.org/en/doc/24e8d315b8229b791ea61e0fa09b28d0-0350012022/original/CMO-April-2022.pdf)
- El FMI documenta un endurecimiento financiero en economías emergentes
  al inicio de la invasión. Sirve para ampliar el juicio contextual de
  `VWO`, con revisión pendiente de derechos de almacenamiento.
  [Perspectivas del FMI, abril de 2022](https://www.imf.org/en/Blogs/Articles/2022/04/19/blog-weo-war-dims-global-economic-outlook-as-inflation-accelerates)
- El estudio del FMI sobre contagio de Bitcoin y acciones se publicó en
  2022. El informe de la IEA sobre demanda eléctrica de centros de datos
  se publicó en 2025. Ambos son candidatos para análisis retrospectivo
  fuera de las respectivas ventanas de publicación del piloto.
  [FMI, Cryptic Connections](https://www.imf.org/en/publications/global-financial-stability-notes/issues/2022/01/10/cryptic-connections-511776) ·
  [IEA, Energy and AI](https://www.iea.org/reports/energy-and-ai/executive-summary)
