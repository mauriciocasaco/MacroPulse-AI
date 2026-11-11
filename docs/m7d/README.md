# M7.D.1 — Selección y suficiencia de evidencia

El componente experimental lee los artefactos editoriales de M7.B mediante `Corpus.load` de M7.C. Valida aprobación, derechos, procedencia y correspondencia de cada afirmación con la revisión. Se aplican los filtros de evento, indicador, geografía, relación y fecha de publicación. El corte es inclusivo y tiene precisión diaria. La publicación, el período de referencia y la vigencia anunciada se evalúan por separado.

El adaptador de preguntas en español e inglés traduce expresiones compatibles a requisitos de medida, población, período, publicación, unidad, institución, modalidad, polaridad y ajuste estadístico. Comprueba que el evento y el ámbito geográfico explícitos concuerden con el alcance de la consulta. El gate contrasta los requisitos con predicados extraídos del enunciado aprobado y conserva los calificadores íntegros. Los candidatos que cubren la pregunta se ordenan por similitud léxica TF-IDF de forma predeterminada. Se puede inyectar un ordenador semántico E5 para comparar resultados. Ambos puntúan únicamente los enunciados de las afirmaciones, conservan los mismos filtros y jamás deciden qué puede respaldar una afirmación.

Se pueden seleccionar varias afirmaciones de un mismo documento o de publicaciones distintas. Una enumeración de períodos como «abril y noviembre de 2020» se evalúa con un requisito por mes. El gate comprueba la publicación de cada dato en la fecha de corte y conserva cualquier edición solicitada. Una expresión como «antes de abril» o «a mediados de abril» delimita otro intervalo. El adaptador actual se abstiene ante esas expresiones. Las medidas de uso de dos tecnologías por separado pueden respaldar respuestas parciales sobre sus proporciones individuales. La proporción de empresas que usaron ambas requiere evidencia de uso conjunto. La misma regla separa las contribuciones contables individuales de una contribución combinada.

## Contrato para M7.D.2

```python
from pathlib import Path

from backend.m7c.corpus import Scope
from backend.m7d import EvidenceAssessment, EvidenceGate

gate = EvidenceGate(Path("docs/m7b"))
scope = Scope(
    event="ukraine",
    indicator="hicp-yoy",
    cutoff="2022-11-17",
    geography="euro-area",
)
result: EvidenceAssessment = gate.assess(
    "How many percentage points did energy contribute to HICP in October 2022?",
    scope,
)
```

`EvidenceAssessment` incluye la versión del contrato, la huella del corpus y el alcance evaluado, también para abstenciones. Se serializa con `model_dump(mode="json")`. Cada `SelectedClaim` incluye IDs de afirmación y documento, título, institución, URL, atribución, URL de derechos, localizador, hashes de la fuente y del bloque, enunciado, calificador, tipo editorial, publicación, período, indicador, geografía, evento, relación y medidas cubiertas.

| Estado | Interpretación para el consumidor |
| --- | --- |
| `sufficient` | Las medidas solicitadas tienen respaldo concreto. El consumidor conserva alcance, modalidad y unidades. |
| `partial` | Hay respaldo para parte de la pregunta o hay una ambigüedad de fecha o edición. El consumidor puede exponer los hechos respaldados y explicitar la limitación. |
| `insufficient` | El gate carece de evidencia para los requisitos de la pregunta o la formulación queda fuera del alcance del intérprete. |
| `unsupported_causal` | Hay observación o contexto pertinente y falta identificación causal. El consumidor puede citar la observación por separado y debe abstenerse de atribuir el efecto. |

`grounding_claims` expone las afirmaciones con `use == "supports_requested_fact"`. En JSON el mismo campo `use` permite distinguirlas de `context_only`. Las relaciones `direct`, `context` y `proxy` se refieren al indicador de la consulta. Una encuesta `proxy` puede sustentar una respuesta sobre su propia población y tecnología, mientras la relación con el PIB sigue siendo indirecta. El tipo editorial distingue observación, interpretación institucional, expectativa e hipótesis. Las expectativas respaldan lo anunciado y conservan su carácter prospectivo. Un conteo absoluto, una tasa de no adopción o un uso restringido a una actividad requieren evidencia de la medida y la población correspondientes. Si una pregunta causal pide el efecto en puntos porcentuales, una tasa observada expresada en porcentaje puede figurar como contexto, con su unidad original. En una pregunta contrafactual la observación histórica también queda como contexto.

Un futuro adaptador confiable puede llamar `assess_intent(question, scope, intent)` con `Intent`, `Requirement`, `Times` y `When`. Debe validar que los requisitos cubran todos los objetos y predicados solicitados y señalar cualquier parte desconocida. El texto de la pregunta sigue sirviendo para ordenar candidatos. El resultado aporta evidencia aprobada para un adaptador futuro a proposiciones de grounding. La integración debe seguir pasando por las validaciones de generación y citas aplicables, incluido `validate_narrative`.

`OfficialMovement` puede recibir el resultado de los cálculos de series existentes con sus unidades, dirección, extremos, cita y procedencia. La validación coteja la serie con el catálogo oficial. Para una consulta histórica el consumidor acredita la disponibilidad de esa versión con `available_on`. Una fecha de observación por sí sola no acredita la disponibilidad de una revisión posterior. Si la versión es desconocida, futura o de otro ámbito, el gate omite el movimiento e informa la limitación. La salida separa `official_movement` de los documentos y de la evaluación causal. Las reglas de cálculo y los KPI existentes se conservan en el runtime.

## Evaluación local

Ejecutar desde la raíz. La rama léxica usa NumPy y los esquemas y filtros de M7.C. Para incluir la comparación semántica, usar la caché local verificada y el entorno descritos en el [manual M7.C](../m7c/README.md). E5 calcula los vectores de las afirmaciones en memoria y ejecuta la inferencia sin red.

```powershell
python -m backend.m7d query 'What was annual HICP in April 2020?' --event covid --indicator hicp-yoy --cutoff 2020-05-20 --geography euro-area
python -m backend.m7d --model-cache $cache evaluate --kind selection --dataset docs/m7d/ranker-selection.jsonl --out docs/m7d/ranker-selection-metrics.json
python -m backend.m7d --model-cache $cache evaluate --kind regression --dataset docs/m7c/evaluation.jsonl --out docs/m7d/metrics.json
```

El [informe de evaluación](evaluation-report.md) explica la elección del ordenador y la comparación con M7.C. Los juicios de selección y validación usan el formato M7.C con `expected_sufficiency` y, cuando corresponde, `required_claim_groups`. Cada grupo requiere al menos un ID para medir cobertura conjunta.

## Límites del piloto

La interpretación libre se limita a predicados, geografías y expresiones temporales reconocidos. Las fechas relativas ambiguas, los modificadores de población, las consultas complejas y los casos con ediciones en conflicto requieren un análisis de intención más completo. El corpus actual carece de identificación causal y de afirmaciones aprobadas sobre spot EUR/USD. La fecha de corte de las publicaciones modela disponibilidad histórica a precisión diaria. La incorporación editorial se realizó después de varios períodos del corpus, de modo que esta evaluación retrospectiva no prueba que el índice experimental existiera en las fechas consultadas.
