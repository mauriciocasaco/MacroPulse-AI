# M7.C — Recuperación híbrida de evidencia histórica

El índice experimental usa el [corpus M7.B](../resources/evidence/m7b/README.md), con 30 documentos y 53 afirmaciones aprobadas. Se puede ejecutar desde la línea de comandos y usar mediante una interfaz Python independiente. El benchmark compara la recuperación híbrida con BM25, E5 y el TF-IDF existente sobre el mismo material. También ejecuta el recuperador actual con sus ocho documentos como referencia de cobertura.

## Ejecutar en Windows

Usar Python 3.12 y un entorno local con las dependencias de M7.C. Si ya está disponible el entorno CPU del piloto de noticias, basta con que incluya las versiones de `requirements-m7c.txt`. Para un entorno nuevo, instalar primero la versión CPU de PyTorch.

```powershell
python -m pip install 'torch==2.15.0+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-m7c.txt
$cache = Read-Host 'Ruta del cache local de Hugging Face'
python -m backend.m7c --model-cache $cache build
python -m backend.m7c --model-cache $cache query '¿Cuánto aportó la energía a la inflación?' --event ukraine --indicator hicp-yoy --cutoff 2022-11-17 --geography euro-area
python -m backend.m7c --model-cache $cache evaluate --out data/m7c/metrics.json
```

`$cache` debe apuntar al directorio que contiene `models--intfloat--multilingual-e5-small`. Si falta la revisión o alguna pieza necesaria, el comando muestra un diagnóstico y termina. La inferencia usa CPU, solo archivos locales, Safetensors y `trust_remote_code=False`, mediante el adaptador existente `backend.news.embeddings.E5Small`. El modelo es `intfloat/multilingual-e5-small`, revisión `614241f622f53c4eeff9890bdc4f31cfecc418b3`, con dimensión 384 y prefijos `query:` y `passage:`. [Ficha de la revisión fijada](https://huggingface.co/intfloat/multilingual-e5-small/tree/614241f622f53c4eeff9890bdc4f31cfecc418b3).

El índice por defecto es `data/m7c/index.sqlite3`, ignorado por Git. `--index` permite elegir otro archivo local. `--corpus` permite apuntar a un directorio M7.B compatible. Los flags comunes van antes del subcomando. `query` devuelve hasta cinco documentos distintos con la mejor afirmación elegible de cada uno, cuando el ranking encuentra una. `--limit 3` reduce el máximo. Para buscar únicamente evidencia directa, usar `--relations direct`.

## Datos e índice

`Corpus.load` valida los esquemas estrictos de M7.B y exige que documentos, afirmaciones, decisiones y revisión editorial coincidan. Comprueba la aprobación, el alcance de derechos, la institución, las URLs de referencia y las huellas de la versión revisada. Cada documento tiene un pasaje de resumen con campos tipados y un pasaje por afirmación, con su calificador. Los IDs de los pasajes llevan el prefijo `summary:` o `claim:` seguido del ID estable de M7.B. El texto enviado al modelo se deriva del resumen o de la afirmación aprobada.

El índice almacena vectores de coma flotante de 32 bits y hashes SHA-256 del pasaje y del vector. La versión del corpus incluye los metadatos aprobados y las decisiones. La huella del modelo incluye revisión, configuración, pesos, tokenizer y versiones de la pila de inferencia. Cada construcción comprueba el SHA-256 de los pesos contra el registrado en el piloto. Se rechazan los pasajes y consultas que superan el límite del tokenizer. No hay truncado silencioso.

La escritura es transaccional. Una reconstrucción reutiliza los vectores íntegros con el mismo contenido y modelo y retira los que quedaron fuera del corpus activo. Una modificación de metadatos invalida la versión del índice aunque permita reutilizar el vector. Una modificación del texto requiere un vector nuevo. El lector abre SQLite en solo lectura y comprueba inventario, versión, dimensión, norma y huellas. La consulta rechaza un índice desactualizado. El alcance de integridad es una caché local con artefactos editoriales de confianza. Los hashes permiten detectar cambios y corrupción accidental.

## Recuperación

La consulta recibe evento, indicador, geografía y fecha de corte como datos estructurados. Las geografías admitidas son zona euro, UE y Estados Unidos, con coincidencia por ámbito. La UE y la zona euro se tratan por separado. La fecha de corte incluye publicaciones de ese día y tiene precisión diaria. Para consultas intradía hará falta registrar la hora de disponibilidad. El período de referencia se conserva como metadato. Una estimación retrospectiva de un trimestre entra al índice histórico a partir de su publicación.

Los filtros se aplican antes de puntuar. Solo se consideran documentos aprobados y afirmaciones asociadas al indicador solicitado. La relación `direct`, `context` o `proxy` forma parte del resultado y admite filtrado. El corpus incluye contexto institucional de EUR/USD para intervalos concretos. Los cuatro documentos de IA aportan medidas de adopción o evidencia de productividad en tareas como contexto indirecto para el PIB. Los comunicados M3 conservan su crecimiento interanual y sus unidades. El HICP distingue tasas y contribuciones en puntos porcentuales. Las decisiones BCE mantienen separadas las fechas de anuncio y entrada en vigor.

BM25 usa un índice invertido, tokenización insensible a acentos y estadísticas de los pasajes elegibles, con `k1=1.2` y `b=0.75`. La rama semántica calcula coseno exacto mediante una matriz de 384 dimensiones sobre el subconjunto filtrado. Cada rama aporta un solo voto por documento, el de su pasaje mejor situado. La fusión RRF suma `1/(60 + rango)` para las ramas en que aparece la fuente. Los empates se resuelven por ID. No se ajustaron parámetros sobre las preguntas de evaluación ni se fijó un umbral semántico. La mejor afirmación por fuente se elige con la misma fusión de sus posiciones relativas a las otras afirmaciones de ese documento.

## Evaluar selección y suficiencia

M7.D usa los juicios congelados de [regresión](../tests/fixtures/retrieval/evaluation.jsonl),
[selección](../tests/fixtures/retrieval/ranker-selection.jsonl) y los conjuntos
reservados de [evaluación](../tests/fixtures/retrieval/holdout.jsonl) y
[regresión del gate](../tests/fixtures/retrieval/evidence-gate-fix-holdout.jsonl).
Los grupos de afirmaciones requieren al menos un ID por grupo para medir
cobertura conjunta. La salida se puede regenerar con el ranking léxico
o con E5 si se pasa `--model-cache $cache` antes del subcomando.

```powershell
New-Item -ItemType Directory -Force data/m7d | Out-Null
python -m backend.m7d evaluate --kind regression --dataset tests/fixtures/retrieval/evaluation.jsonl --out data/m7d/metrics.json
python -m backend.m7d evaluate --kind selection --dataset tests/fixtures/retrieval/ranker-selection.jsonl --out data/m7d/ranker-selection-metrics.json
python -m backend.m7d evaluate --kind holdout --dataset tests/fixtures/retrieval/holdout.jsonl --out data/m7d/holdout-metrics.json
```

## Pruebas

```powershell
$env:M7C_TEST_MODEL_CACHE = $cache
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
python -m pytest -q tests/test_m7c.py tests/test_m7d.py
```

Las pruebas de corpus y ranking se ejecutan sin pesos. La prueba E5 usa el modelo real de la caché y dos construcciones independientes. También verifica reutilización, invalidación, actualización selectiva, rollback, corrupción y consulta.
