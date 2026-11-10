# M6.2A · Conexión directa a Supabase DEV

Verificación del 10 de noviembre de 2026. Un participante en el alcance SIMPLE. Modelo/esfuerzo efectivos: no verificables. La auditoría independiente corresponde a M6.2B.

## Implementación

- `backend/macro_data/api.py` mantiene el modo predeterminado `loopback` con `MACRO_API_DSN`. El modo explícito `supabase-dev` reutiliza el lector dotenv y el validador DEV existentes.
- `backend/public_demo.py` rechaza la configuración `MACRO_API_MODE` en el demo público.
- `tests/test_macro_api.py` verifica la selección loopback, la ruta DEV, el escapado de contraseñas, el rechazo de destinos, puertos y roles no autorizados, las variables de libpq, TLS, certificados, configuración incompleta y la sanitización de errores.
- `supabase/macro_read_policies.sql` registra las cuatro políticas comprobadas en DEV. Cada política es permisiva y se limita a `FOR SELECT TO marketpulse_api_ro USING (true)`. El script reemplaza las cuatro definiciones por nombre dentro de una transacción, por lo que puede repetirse en futuros entornos. Requiere las tablas con RLS activa, el rol y sus privilegios existentes. El script no contiene grants ni políticas para `anon`, `authenticated` o `PUBLIC` y queda fuera de la ruta de migraciones automáticas.

El lector remoto consume `MACRO_API_MODE`, `MARKETPULSE_DEV_PROJECT_REF`, `USER_RO`, `PASS_USER_RO`, `HOST_RO`, `PORT_RO`, `DB_RO`, `SSL_MODE_RO` y `SSL_ROOT_CERT_RO`. El proceso tiene precedencia sobre el `.env` ignorado de la raíz. El lector abre ese archivo bajo demanda, sin interpolación. `MARKETPULSE_DISABLE_LOCAL_DOTENV` permite desactivar su lectura. Uvicorn arranca con la forma loopback exigida por las rutas locales.

El validador compartido limita el destino al hostname directo del proyecto DEV o al dominio del pooler de Supabase con el sufijo de ese proyecto en el usuario. MacroPulse admite únicamente el rol de lectura previsto, la base esperada y el puerto 5432. Rechaza variables heredadas de libpq, usa `make_conninfo`, carga el certificado raíz fijado por el repositorio y exige TLS `verify-full`. El conector comprueba el uso de TLS y el destino efectivo. La transacción conserva `READ ONLY` y `REPEATABLE READ`. Los fallos de configuración o conexión generan mensajes locales fijos y un 503 público genérico.

## Evidencia

- Supabase DEV autenticó al rol previsto. TLS activo, configuración `verify-full`, `transaction_read_only=on` y `transaction_isolation=repeatable read`. El certificado se pudo cargar y la verificación TLS completó.
- Tras la creación manual de las políticas por el usuario, los `SELECT count(*)` del rol devolvieron 5 filas en `series`, 7 en `capture`, 2.253 en `observation_revision` y 2.253 en `revision_capture`. RLS seguía activa en las cuatro tablas. `pg_policies` confirmó las cuatro políticas esperadas, solo para el rol de lectura y el comando `SELECT`.
- Se reutilizaron los servidores locales de M6.2A en los puertos 8002 y 5180. Tanto FastAPI como Vite devolvieron HTTP 200 en el catálogo y en los cinco historiales. Se compararon los cuerpos JSON y coincidieron en ambos accesos. Vite sirvió la aplicación con HTTP 200. La validación visual humana queda pendiente.
- Backend con `.venv/Scripts/python -m pytest -q -rs` obtuvo 335 aprobadas y 82 omitidas por DSN desechables ausentes o falta de `langgraph`. Hubo una advertencia de deprecación de Starlette. Frontend obtuvo 57 aprobadas y Worker 6 aprobadas. `git diff --check` pasó y `.env` sigue ignorado y sin seguimiento.
- La prueba remota ejecutó consultas de lectura con el rol existente. El script de políticas se cotejó estáticamente con `pg_policies` y no se ejecutó. El caso del máximo de las tasas del BCE queda pendiente fuera de esta etapa.

Historiales para la ventana solicitada 2017-01-01 a 2026-11-10. El rango muestra el inicio del primer período y el fin del último período devuelto.

| Indicador | Observaciones | Rango devuelto | FastAPI / Vite |
|---|---:|---|---|
| `eur-usd` | 2.012 | 2019-01-02 a 2026-11-09 | 200 / 200 |
| `m3-stock` | 93 | 2019-01-01 a 2026-09-30 | 200 / 200 |
| `deposit-rate` | 21 | 2019-09-18 a 2026-09-16 | 200 / 200 |
| `real-gdp-qoq` | 31 | 2019-01-01 a 2026-09-30 | 200 / 200 |
| `hicp-yoy` | 94 | 2019-01-01 a 2026-10-31 | 200 / 200 |

## PowerShell 7 y Chrome

Los servidores verificados están en 8002 y 5180. Si necesitas reiniciarlos y los puertos están libres, ejecuta en una terminal:

```powershell
Set-Location 'C:\Users\Usuario\Documents\MarketPulse-AI'
$env:MACRO_API_MODE = 'supabase-dev'
$env:MARKETPULSE_DEV_PROJECT_REF = 'swpithkjviavycrxpwed'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'false'
.\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8002
```

En otra terminal, con las dependencias de frontend instaladas:

```powershell
Set-Location 'C:\Users\Usuario\Documents\MarketPulse-AI\frontend'
$env:MARKETPULSE_API_PORT = '8002'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'false'
npm run dev -- --port 5180
```

En una tercera terminal:

```powershell
$base = 'http://127.0.0.1:5180/api/macro/indicators'
Invoke-RestMethod $base | ForEach-Object {
    $history = Invoke-RestMethod "$base/$($_.id)/history?start=2017-01-01&end=2026-11-10"
    [pscustomobject]@{ Series = $_.id; Observations = $history.observations.Count }
}
Start-Process chrome.exe 'http://127.0.0.1:5180/'
```

## Estado Git

Rama `codex/macropulse-migration`, HEAD `c36cc26fbb2d68cd306ea87be727034f090383f0`. M6.2A comprende tres archivos de código y pruebas modificados, el script SQL y este informe sin seguimiento. Se preservaron las modificaciones previas en `backend/macro_ai/corpus.json`, `backend/macro_ai/service.py` y `tests/test_macro_ai.py`, y los informes sin seguimiento `docs/macro/m6-1-independent-audit.md` y `docs/macro/m6-independent-audit.md`.

**M6.2A IMPLEMENTATION: PASS**

**SUPABASE DEV: CONNECTED**

**MACRO SERIES AVAILABLE: 5/5**

**FASTAPI + VITE: PASS**

**LOCAL TESTS: PARTIAL**

**INDEPENDENT SECURITY AUDIT: PENDING (M6.2B)**

**M6.2 CODE GATE: OPEN**

**PUBLIC RELEASE GATE: OPEN**
