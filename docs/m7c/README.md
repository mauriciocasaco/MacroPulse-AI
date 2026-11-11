# M7.C — Recuperación híbrida de evidencia histórica

El índice experimental contiene 24 resúmenes y 47 afirmaciones aprobadas en M7.B. Se puede ejecutar desde la línea de comandos y usar mediante una interfaz Python independiente. El benchmark compara la recuperación híbrida con BM25, E5 y el TF-IDF existente sobre el mismo material. También ejecuta el recuperador actual con sus ocho documentos como referencia de cobertura.

## Ejecutar en Windows

Usar Python 3.12 y un entorno local con las dependencias de M7.C. Si ya está disponible el entorno CPU del piloto de noticias, basta con que incluya las versiones de `requirements-m7c.txt`. Para un entorno nuevo, instalar primero la versión CPU de PyTorch.

```powershell
python -m pip install 'torch==2.15.0+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-m7c.txt
$cache = Join-Path $env:TEMP 'marketpulse-hf-7db692d'
python -m backend.m7c --model-cache $cache build
python -m backend.m7c --model-cache $cache query '¿Cuánto aportó la energía a la inflación?' --event ukraine --indicator hicp-yoy --cutoff 2022-11-17 --geography euro-area
python -m backend.m7c --model-cache $cache evaluate --out data/m7c/metrics.json
```

`$cache` debe apuntar al directorio que contiene `models--intfloat--multilingual-e5-small`. La ruta de ejemplo fue la caché existente usada en la evaluación. Si falta la revisión o alguna pieza necesaria, el comando muestra un diagnóstico y termina. La inferencia usa CPU, solo archivos locales, Safetensors y `trust_remote_code=False`, mediante el adaptador existente `backend.news.embeddings.E5Small`. El modelo es `intfloat/multilingual-e5-small`, revisión `614241f622f53c4eeff9890bdc4f31cfecc418b3`, con dimensión 384 y prefijos `query:` y `passage:`. [Ficha de la revisión fijada](https://huggingface.co/intfloat/multilingual-e5-small/tree/614241f622f53c4eeff9890bdc4f31cfecc418b3).

El índice por defecto es `data/m7c/index.sqlite3`, ignorado por Git. `--index` permite elegir otro archivo local. `--corpus` permite apuntar a un directorio M7.B compatible. Los flags comunes van antes del subcomando. `query` devuelve hasta cinco documentos distintos con la mejor afirmación elegible de cada uno, cuando el ranking encuentra una. `--limit 3` reduce el máximo. Para buscar únicamente evidencia directa, usar `--relations direct`.

## Datos e índice

`Corpus.load` valida los esquemas estrictos de M7.B y exige que documentos, afirmaciones, decisiones y revisión editorial coincidan. Comprueba la aprobación, el alcance de derechos, la institución, las URLs de referencia y las huellas de la versión revisada. Cada documento tiene un pasaje de resumen con campos tipados y un pasaje por afirmación, con su calificador. Los IDs de los pasajes llevan el prefijo `summary:` o `claim:` seguido del ID estable de M7.B. El texto enviado al modelo se deriva del resumen o de la afirmación aprobada.

El índice almacena vectores de coma flotante de 32 bits y hashes SHA-256 del pasaje y del vector. La versión del corpus incluye los metadatos aprobados y las decisiones. La huella del modelo incluye revisión, configuración, pesos, tokenizer y versiones de la pila de inferencia. Cada construcción comprueba el SHA-256 de los pesos contra el registrado en el piloto. Se rechazan los pasajes y consultas que superan el límite del tokenizer. No hay truncado silencioso.

La escritura es transaccional. Una reconstrucción reutiliza los vectores íntegros con el mismo contenido y modelo y retira los que quedaron fuera del corpus activo. Una modificación de metadatos invalida la versión del índice aunque permita reutilizar el vector. Una modificación del texto requiere un vector nuevo. El lector abre SQLite en solo lectura y comprueba inventario, versión, dimensión, norma y huellas. La consulta rechaza un índice desactualizado. El alcance de integridad es una caché local con artefactos editoriales de confianza. Los hashes permiten detectar cambios y corrupción accidental.

## Recuperación

La consulta recibe evento, indicador, geografía y fecha de corte como datos estructurados. Las geografías admitidas son zona euro, UE y Estados Unidos, con coincidencia por ámbito. La UE y la zona euro se tratan por separado. La fecha de corte incluye publicaciones de ese día y tiene precisión diaria. Para consultas intradía hará falta registrar la hora de disponibilidad. El período de referencia se conserva como metadato. Una estimación retrospectiva de un trimestre entra al índice histórico a partir de su publicación.

Los filtros se aplican antes de puntuar. Solo se consideran documentos aprobados y afirmaciones asociadas al indicador solicitado. La relación `direct`, `context` o `proxy` forma parte del resultado y admite filtrado. M7.B aún no contiene evidencia aprobada de EUR/USD. Los cuatro documentos de IA aportan medidas de adopción o evidencia de productividad en tareas como contexto indirecto para el PIB. Los comunicados M3 conservan su crecimiento interanual y sus unidades. El HICP distingue tasas y contribuciones en puntos porcentuales. Las decisiones BCE mantienen separadas las fechas de anuncio y entrada en vigor.

BM25 usa un índice invertido, tokenización insensible a acentos y estadísticas de los pasajes elegibles, con `k1=1.2` y `b=0.75`. La rama semántica calcula coseno exacto mediante una matriz de 384 dimensiones sobre el subconjunto filtrado. Cada rama aporta un solo voto por documento, el de su pasaje mejor situado. La fusión RRF suma `1/(60 + rango)` para las ramas en que aparece la fuente. Los empates se resuelven por ID. No se ajustaron parámetros sobre las preguntas de evaluación ni se fijó un umbral semántico. La mejor afirmación por fuente se elige con la misma fusión de sus posiciones relativas a las otras afirmaciones de ese documento.

## Integración futura con M7.D

```python
from pathlib import Path

from backend.m7c.corpus import Corpus, Scope
from backend.m7c.index import LocalE5
from backend.m7c.retrieval import HybridRetriever

retriever = HybridRetriever(
    Corpus.load(Path("docs/m7b")),
    Path("data/m7c/index.sqlite3"),
    LocalE5(Path("ruta/al/cache")),
)
hits = retriever.search("How did energy contribute to inflation?", Scope(
    event="ukraine", indicator="hicp-yoy", cutoff="2022-11-17",
    geography="euro-area", relations=("direct",),
), limit=3)
```

El adaptador de M7.D puede recibir el alcance del intérprete de consultas y pasar al grounding la afirmación revisada, el tipo, los calificadores, el enlace, el localizador y los hashes. El recuperador entrega contexto potencial. La cobertura de la pregunta y la suficiencia de la afirmación siguen requiriendo comprobación antes de responder, especialmente cuando el índice contiene documentos del ámbito correcto pero ninguno responde la pregunta. Las observaciones de series siguen siendo la fuente para movimientos de indicadores. Conviene probar la calidad de recuperación y la abstención con un conjunto distinto antes de integrar.

Para persistencia futura, conservar el registro de modelos y el patrón de versionado por huella de `news.embedding_models` y `news.record_embeddings`. Una tabla de pasajes MacroPulse puede usar los IDs de M7.B y `extensions.vector(384)` sin acoplar la identidad editorial a IDs de artículos de noticias. Mantener evento, indicador, geografía, estado editorial y fecha de publicación como filtros indexables. El coseno exacto sobre el subconjunto filtrado es la primera referencia para comparar índices aproximados cuando el tamaño lo justifique. Medir recall y latencia antes de adoptar HNSW. Mantener el registro editorial y las restricciones de derechos en la misma lectura consistente que el ranking. [Documentación de pgvector](https://github.com/pgvector/pgvector).

## Pruebas

```powershell
$env:M7C_TEST_MODEL_CACHE = $cache
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
python -m pytest -q tests/test_m7c.py
```

Las pruebas de corpus y ranking se ejecutan sin pesos. La prueba E5 usa el modelo real de la caché y dos construcciones independientes. También verifica reutilización, invalidación, actualización selectiva, rollback, corrupción y consulta. El [informe de evaluación](evaluation-report.md) resume los resultados y enlaza las trazas por caso.
