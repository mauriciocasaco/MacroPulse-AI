# MacroPulse AI — integración final A/B/C

Fecha: 2026-11-10. MANUAL, un participante, sin subagentes ni auditoría independiente. Modelo/esfuerzo efectivos: no verificables. Host: danger-full-access, aprobación never; sin cambios de política.

## Baseline y resultado

Checkout: `C:/Users/Usuario/Documents/MarketPulse-AI`. Rama autorizada `codex/macropulse-migration`, HEAD `1829826fdfcbd06023840c50ccd6c0b6adc34d13`. Estado inicial limpio. Referencias locales: esa rama en HEAD, main en `06fcad69370fe60de9121e9e19554819b9282a00`. Se verificaron `git worktree list --porcelain`, HEAD y estado completo de A=d2fc, B=c940, C=adf0, todos detached en el mismo baseline. El worktree adicional dfb0 quedó fuera del alcance.

Se integraron secuencialmente A → B → C mediante parches comprobados y copias selectivas de archivos nuevos. No se modificaron los orígenes, rama, índice, AGENTS.md, credenciales, RLS ni Supabase. No hubo commit, merge, push, publicación o migración. Los hashes SHA-256 de los archivos de entrega de los orígenes permanecieron iguales durante la verificación final; no se ejecutó ninguna escritura en esos worktrees. No se leyeron archivos .env.

- A: única UI MacroPulse, eliminación de navegación Market Archive, selectores Yahoo y componentes legacy. Enlaces antiguos abren macro. Vite/PWA y Worker rechazan rutas legacy y conservan controles macro. Los endpoints internos legacy del backend permanecen; no están expuestos por esos proxies.
- B: cinco nombres y colores exactos, seis presets, dominios compactos, continuidad entre valores válidos, frecuencias/decimales originales, tasa BCE escalonada dentro de cobertura verificada. La investigación MAX conserva el límite de 3.660 días y la UI informa su ventana cuando difiere del gráfico.
- C: título RAG-Powered Macro Research, tres botones parametrizados por indicador, campo editable y envío exclusivamente mediante Ask. Se preservan intención, validaciones institucionales, fechas, evidencia, citas y abstención de LangGraph.

## Solapamientos y correcciones

- `MacroWorkspace.test.tsx`: pruebas relevantes A/B/C y M6 conservadas, retirando el mock del componente eliminado. C ahora espera la carga asíncrona introducida por B. Se amplió a las quince combinaciones en App real y a un envío personalizado con el período seleccionado.
- `MacroWorkspace.tsx`: estado/carga/gráficos B combinado con `indicatorId={id}` de C, conservando selección, presets y ventana de investigación.
- `styles.css`: reglas legacy A retiradas y estilos B integrados por selector; reglas de temas y responsive preservadas.
- No hubo otros solapamientos entre archivos de entrega. No se reemplazaron archivos compartidos completos para resolver diferencias.
- Corrección adicional: los bloques de 3.660 días de B superaban el límite público de menos de 2.600 días. El cliente usa bloques de 2.599 días; límites Worker/backend intactos. Una prueba recorre el Worker real con origen simulado para los cinco indicadores y seis presets.
- Se deduplican por `period_raw` observaciones mensuales/trimestrales que intersectan bloques contiguos, preservando sus valores originales. Regresión sintética incluida.

## Verificación reproducible

Todos los proveedores de aplicación se simularon. No hubo llamadas reales a Gemini, Supabase ni servicios de producción.

| Comando | Resultado |
|---|---|
| `npm test -- --reporter=dot` | 98 aprobadas, 6 archivos |
| `npm run test:worker` | 6 aprobadas |
| `npm run lint` | PASS sin advertencias |
| `npm run typecheck` | PASS |
| `npm run build` | PASS frontend/PWA |
| `npm run build:public` | PASS frontend/PWA public-demo |
| `python -m pytest -q --tb=short` | 492 aprobadas, 81 omitidas |
| `pytest tests/test_macro_ai.py -q -k 'm6_high or historical_wrappers or unsupported_rate_authority'` | 54 aprobadas, 103 no seleccionadas |
| `git diff --check` | PASS |

Backend ejecutado con `MARKETPULSE_DISABLE_LOCAL_DOTENV=1`, `MARKETPULSE_MODE=local`, mediante `.venv/Scripts/python.exe -m pytest`. Las 81 omisiones requieren DSN de PostgreSQL desechable en loopback: MARKET_API_TEST_DSN, MACRO_DATA_TEST_DSN, MARKET_DATA_TEST_DSN y MARKETPULSE_NEWS_LOCAL_DSN. No equivalen a pruebas aprobadas ni validación remota.

Los tres fallos TLS históricos no reaparecieron: el CA fijado `Certificate/prod-ca-2021.crt` ya existe en el principal y las pruebas del lector remoto con conexión simulada pasaron. No se copió ni cambió el CA ni se debilitó TLS.

Smoke HTTP con Vite real en 5182 y origen sintético en 8004: seis combinaciones método/ruta legacy devolvieron 404 sin forwarding; enlace `?workspace=market&asset=GC%3DF` 200; catálogo y cinco historiales macro 200; POST macro desde origen 5182 200; origen 5181 y externo 403 sin forwarding. Siete solicitudes al origen simulado. Ambos servidores temporales se cerraron; servicios preexistentes intactos.

Regresión HIGH: seis casos nuevos HTTP con preguntas explícitas Fed/Banco de Inglaterra en inglés/español y con eventos. Devuelven `insufficient_evidence`, sin hechos, cálculos, interpretación ni fuentes BCE. Los proveedores de estadísticas y Gemini hacen fallar la prueba si se invocan. Pasan también 30 regresiones institucionales previas y 18 envolturas históricas. PASS para esta regresión reproducible; sin afirmar una auditoría independiente o cobertura de toda formulación posible.

## Límites

El corpus de ocho resúmenes institucionales no se amplió. Con estadísticas sintéticas completas, cierre 2023-12-31 y corpus actual: COVID aporta contexto para EUR/USD, M3, PIB e inflación; Ucrania para inflación. Los demás pares se abstienen. No hay material de IA generativa ni contexto suficiente para tasa BCE en esos eventos. Las quince combinaciones recorren además el grafo completo con corpus sintético separado. Ningún resultado identifica causalidad.

El corte documental evita publicaciones posteriores al cierre, pero las series capturadas pueden contener revisiones: no reconstruye información histórica disponible en tiempo real. MAX combina capturas de varias solicitudes, sin snapshot atómico; puede necesitar 18 solicitudes por indicador desde 1900 y está sujeto a cuotas públicas preservadas. La ventana RAG MAX puede ser menor que la del gráfico y la UI lo comunica.

Temas, contraste, responsive y PWA tienen comprobaciones de código/build. La validación visual humana y la instalación real siguen pendientes. No se conectaron bases DEV/producción.

## Arranque local con Gemini simulado — PowerShell 7

Requiere `MACRO_API_DSN` ya configurado mediante un mecanismo seguro para una base PostgreSQL local autorizada, poblada y de solo lectura. No introducir su valor en documentación ni usar un DSN DEV/remoto. Estos comandos no leen .env ni inventan observaciones del gráfico. El generador simulado selecciona citas del contexto validado para revisar el flujo RAG; no valida Gemini real.

Terminal 1:

```powershell
Set-Location 'C:\Users\Usuario\Documents\MarketPulse-AI'
$env:MARKETPULSE_DISABLE_LOCAL_DOTENV = '1'
$env:MARKETPULSE_MODE = 'local'
$env:MACRO_API_MODE = 'loopback'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'true'
if (-not $env:MACRO_API_DSN) { throw 'Configura MACRO_API_DSN de solo lectura en loopback.' }
@'
import json
from pathlib import Path
import uvicorn
from backend.macro_ai import service
from backend.macro_ai.retrieval import CorpusIndex

def mock_gemini(prompt, evidence):
    context = json.loads(evidence)
    return {"selections": [{"citations": [
        context["statistics"][0]["citations"][0], context["documents"][0]["id"]
    ]}]}

workflow = service.Workflow(CorpusIndex.load(Path('backend/macro_ai/corpus.json')), generate=mock_gemini)
service.workflow = lambda: workflow
uvicorn.run('backend.api:app', host='127.0.0.1', port=8004)
'@ | .\.venv\Scripts\python.exe -
```

Terminal 2:

```powershell
Set-Location 'C:\Users\Usuario\Documents\MarketPulse-AI\frontend'
$env:MARKETPULSE_API_PORT = '8004'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'true'
Remove-Item Env:MARKETPULSE_LAN_IP -ErrorAction SilentlyContinue
npm run dev -- --port 5182
```

URL tras arrancar: [MacroPulse local](http://127.0.0.1:5182/). El enlace [legacy](http://127.0.0.1:5182/?workspace=market) también debe abrir MacroPulse. No se dejó servidor nuevo ejecutándose. Se evitan los puertos preexistentes 8002, 8003 y 5181 observados durante la integración.

## Inventario Git

Los tres informes fuente se incorporaron sin reescribir sus resultados históricos. Este informe documenta la verificación conjunta. HEAD y rama sin cambios; todo sin staging.

```text
 M backend/macro_ai/service.py
 M frontend/pwa/local-read.test.ts
 M frontend/pwa/local-read.ts
 D frontend/src/Analysis.tsx
 M frontend/src/App.test.tsx
 M frontend/src/App.tsx
 M frontend/src/MacroResearch.test.tsx
 M frontend/src/MacroResearch.tsx
 M frontend/src/MacroWorkspace.test.tsx
 M frontend/src/MacroWorkspace.tsx
 D frontend/src/PriceChart.tsx
 M frontend/src/styles.css
 M frontend/vite.config.ts
 M frontend/worker/public-demo.mjs
 M frontend/worker/public-demo.node-test.mjs
 M tests/test_macro_ai.py
?? docs/macro/final-integration.md
?? docs/macro/ux-worktree-a.md
?? docs/macro/worktree-b-indicators-charts.md
?? docs/macro/worktree-c-rag-ux.md
?? frontend/src/MacroPlot.tsx
?? frontend/src/macro-chart.test.tsx
?? frontend/src/macro-chart.ts
```
