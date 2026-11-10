# M6.3 · Validación local de MacroPulse AI

Validación del 10 de noviembre de 2026 en `codex/macropulse-migration`, HEAD `c36cc26fbb2d68cd306ea87be727034f090383f0`. Modo SIMPLE con un participante. Modelo/esfuerzo efectivos de Codex: no verificables.

## Configuración y flujo

- FastAPI y Vite se ejecutaron en loopback, en 8003 y 5181. Los servidores de M6.2A ya ocupaban 8002 y 5180 y siguieron disponibles.
- Variables de activación y conexión: `MARKETPULSE_MACRO_AI_ENABLED`, `MARKETPULSE_API_PORT`, `MACRO_API_MODE`, `MARKETPULSE_DEV_PROJECT_REF`, `USER_RO`, `PASS_USER_RO`, `HOST_RO`, `PORT_RO`, `DB_RO`, `SSL_MODE_RO`, `SSL_ROOT_CERT_RO`. Para Gemini: `GEMINI_API_KEY` y, opcionalmente, `MARKETPULSE_GEMINI_MODEL`. El modelo efectivo fue `gemini-3.6-flash`.
- Se instalaron las dependencias fijadas en `backend/requirements-ai.txt` en la `.venv` existente. El endpoint local `POST /macro/analysis` inicializa el grafo compilado de LangGraph 1.2.20 y el índice del corpus institucional al usarlos.
- El grafo reconoce el tema, selecciona indicadores, calcula estadísticas con el lector Supabase DEV de M6.2A y recupera documentos del corpus local. Envía a Gemini solo el tema normalizado, el período, estadísticas públicas y resúmenes aprobados. Gemini selecciona referencias con un máximo de 500 tokens y sin reintentos. El backend valida que las referencias existan y compone la respuesta a partir de la evidencia.
- Vite comprobaba `Origin` contra el puerto predeterminado aunque se iniciara con `--port`. Se ajustó la comparación al puerto configurado. Los POST del origen 5181 completaron el flujo y el origen 5180 recibió HTTP 403 en ese proxy. El formulario sigue excluido del demo público.

## Consultas reales

Tres llamadas a Gemini, todas a través del proxy Vite, con HTTP 200 y referencias verificadas. Dos formulaciones adicionales se abstuvieron antes de contactar al proveedor.

| Consulta | Ventana | Resultado |
|---|---|---|
| ¿Cómo evolucionó la inflación interanual de la eurozona durante 2025? | 2025-01-01 a 2025-12-31 | Respondió. La serie IAPC mostró una caída de 0,5 puntos porcentuales entre enero y diciembre. El documento de Eurostat de 2022 se presentó como contexto fechado. |
| ¿Cuál fue el máximo de la tasa de depósito del BCE entre 2022 y 2025? | 2022-01-01 a 2025-12-31 | Abstención del selector. Se reprodujo también la regresión conocida con «¿Qué máximo alcanzaron las tasas de interés del BCE?» mediante el selector local. |
| How did the ECB deposit facility rate change between 2022 and 2025? | 2022-01-01 a 2025-12-31 | Respondió con la serie de depósito y documentos del BCE. La diferencia calculada fue 250 puntos básicos entre el ancla efectiva de 2019, vigente al inicio de la ventana, y el último cambio observado en junio de 2025. |
| ¿Qué relación temporal muestran M3 y la inflación en el período disponible? | 2019-01-01 a 2025-12-31 | Abstención del selector. |
| What relationship do M3 and inflation show? | 2019-01-01 a 2025-12-31 | Respondió con ambas series y un documento del BCE. Pearson contemporáneo de −0,27 en 72 pares mensuales, de 2020-01 a 2025-12. El resultado es descriptivo y no estima rezagos ni causalidad. |

Se conservaron el selector y el corpus existentes. Las pruebas de componentes comprueban el envío, la carga, las fuentes, los resultados y la presentación de abstenciones y errores. La revisión visual humana en Chrome queda pendiente.

## Pruebas

- Backend: `python -m pytest -q -rs`, 450 aprobadas y 81 omitidas por falta de DSN de pruebas desechables.
- Frontend: `npm test`, 59 aprobadas. `npm run typecheck` pasó. Worker: `npm run test:worker`, 6 aprobadas.
- Se preservaron los cambios previos de M6.1 y M6.2A. Esta etapa modificó `frontend/vite.config.ts`, `frontend/src/MacroResearch.test.tsx` y este informe.

## PowerShell 7

Los servicios verificados quedaron en 8003 y 5181. Si necesitas reiniciarlos, usa dos terminales. En la primera:

```powershell
Set-Location 'C:\Users\Usuario\Documents\MarketPulse-AI'
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt
$env:MACRO_API_MODE = 'supabase-dev'
$env:MARKETPULSE_DEV_PROJECT_REF = 'swpithkjviavycrxpwed'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'true'
@'
import os, subprocess, sys
from dotenv import dotenv_values

settings = dotenv_values('.env', encoding='utf-8-sig', interpolate=False)
for key in ('GEMINI_API_KEY', 'MARKETPULSE_GEMINI_MODEL'):
    if settings.get(key):
        os.environ.setdefault(key, settings[key])
if not os.environ.get('GEMINI_API_KEY', '').strip():
    sys.exit('GEMINI_API_KEY unavailable')
sys.exit(subprocess.call([
    sys.executable, '-m', 'uvicorn', 'backend.api:app',
    '--host', '127.0.0.1', '--port', '8003',
]))
'@ | .\.venv\Scripts\python.exe -
```

En la segunda:

```powershell
Set-Location 'C:\Users\Usuario\Documents\MarketPulse-AI\frontend'
$env:MARKETPULSE_API_PORT = '8003'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'true'
Remove-Item Env:MARKETPULSE_LAN_IP -ErrorAction SilentlyContinue
npm run dev -- --port 5181
```

Abre `http://127.0.0.1:5181/` en Chrome. En **Macro research**, fija el período del 1 de enero al 31 de diciembre de 2025 y pulsa **Apply**. En **Ask about macro data**, pregunta «¿Cómo evolucionó la inflación interanual de la eurozona durante 2025?».

**M6.3 LOCAL AI: PASS**

**SUPABASE DEV: CONNECTED**

**LANGGRAPH: WORKING**

**RAG: WORKING**

**GEMINI LIVE: WORKING**

**FRONTEND AI: ENABLED**

**HUMAN FUNCTIONAL VALIDATION: PENDING**

**PUBLIC RELEASE: NOT AUTHORIZED**
