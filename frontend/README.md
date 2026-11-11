# MacroPulse AI · Market archive and local PWA

For the primary macroeconomic workspace, see the [root setup guide](../README.md#local-development).
The asset workspace described below is available at `/?workspace=market`.

## Inicio en Windows

Usá Python 3.12, PowerShell 7.4+ y una versión LTS de Node compatible con `package.json`, por ejemplo Node 24.15 o posterior dentro de la rama 24. Abrí las dos terminales en la raíz del repositorio.

**Terminal A · FastAPI**

Prepará el entorno de Python si todavía no está instalado.

```powershell
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Para consultar DEV, usá el DSN previamente autorizado de un rol de lectura con acceso al catálogo, precios y evidencia. El hostname debe coincidir con el certificado del servidor. Configurá la CA de confianza con `sslrootcert` en el DSN o en la ubicación estándar de libpq. FastAPI impone `sslmode=verify-full` y transacciones de solo lectura. El ingreso enmascarado mantiene el DSN en el entorno de esta terminal durante la ejecución. [Contrato del API](../docs/market-data-api.md)

```powershell
$env:MARKET_API_DSN = Read-Host -MaskInput 'DSN autorizado de solo lectura de Supabase DEV'
try {
    .\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
} finally {
    Remove-Item Env:MARKET_API_DSN -ErrorAction SilentlyContinue
}
```

**Terminal B · Vite**

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

Cuando Vite indique que está listo, abrí la interfaz desde otra terminal o desde la barra de direcciones del navegador.

```powershell
Start-Process 'http://127.0.0.1:5173/?workspace=market'
```

Vite usa el puerto fijo `5173` en loopback. Si el puerto `8000` ya está ocupado, iniciá FastAPI con `--port 8001` y ejecutá `$env:MARKETPULSE_API_PORT = '8001'` en la terminal B antes de iniciar Vite. Para detener ambos procesos, presioná **Ctrl+C** en cada terminal. Si usaste un puerto alternativo, podés limpiar esa variable con `Remove-Item Env:MARKETPULSE_API_PORT`.

## iPhone testing on a private LAN

Use PowerShell 7.4 or later. Connect the iPhone to the same trusted private
subnet as the PC. On the iPhone, open **Settings → Wi-Fi → ⓘ** next to the
connected network and note its IPv4 address. In PowerShell, use
`Get-NetIPConfiguration` to find the PC's IPv4 address on that subnet.

From a fresh PowerShell window at the repository root, install the local
dependencies if needed:

```powershell
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt
Push-Location frontend
try { npm.cmd ci } finally { Pop-Location }
```

Run the following from the same repository root. It opens separate PowerShell
windows for FastAPI and Vite. Enter the authorized read-only loopback PostgreSQL
DSN and Gemini key at the masked prompts in the FastAPI window. If you already
use an authorized read-only DEV configuration, follow its
[backend setup](../docs/macro/m6-3-local-ai-validation.md#powershell-7)
for the database settings and keep the exact Uvicorn host and port shown below.

```powershell
$pcIp = Read-Host 'PC private IPv4 address'
$phoneIp = Read-Host 'iPhone IPv4 address'
$repoPath = (Get-Location).Path
$sharedLan = @{
    MARKETPULSE_LAN_DEV = 'true'
    MARKETPULSE_LAN_IP = $pcIp
    MARKETPULSE_LAN_TOKEN = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    MARKETPULSE_MACRO_AI_ENABLED = 'true'
}
$backendLan = $sharedLan + @{ MARKETPULSE_MODE = 'local'; MACRO_API_MODE = 'loopback' }
$frontendLan = $sharedLan + @{ MARKETPULSE_LAN_DEVICE_IP = $phoneIp; MARKETPULSE_API_PORT = '8000' }
$backendCommand = @'
$env:MACRO_API_DSN = Read-Host -MaskInput 'Authorized read-only loopback PostgreSQL DSN'; $env:GEMINI_API_KEY = Read-Host -MaskInput 'Authorized Gemini API key'; .\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
'@
try {
    Start-Process pwsh -WorkingDirectory $repoPath -Environment $backendLan -ArgumentList '-NoProfile', '-Command', $backendCommand
    Start-Process pwsh -WorkingDirectory (Join-Path $repoPath 'frontend') -Environment $frontendLan -ArgumentList '-NoProfile', '-Command', 'npm.cmd run dev'
} finally {
    Remove-Variable sharedLan,backendLan,frontendLan
}
Write-Host "Open in iPhone Safari: http://${pcIp}:5173/"
```

Wait for both servers to start. Open the printed HTTP URL in Safari. Choose an
**Indicator**, change the period under **Official history**, and touch the chart
to inspect an observation. In **RAG-Powered Macro Research**, select EUR/USD and
the **COVID-19** event. Enter “How did the euro fare against the dollar during
coronavirus?” in **Research question** and tap **Ask LLM Model**. The proxy
accepts three research submissions per rolling minute. If it returns HTTP 429,
wait for the `Retry-After` interval (up to one minute) before retrying.

If Windows Firewall blocks the connection on a network already set to
**Private**, an administrator can add a temporary device-scoped rule. Enter
the same PC and iPhone addresses when prompted.

```powershell
New-NetFirewallRule -Name 'MarketPulse-LAN-Dev-5173' -DisplayName 'MarketPulse LAN development' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5173 -Profile Private `
    -LocalAddress (Read-Host 'PC private IPv4 address') -RemoteAddress (Read-Host 'iPhone IPv4 address')
```

The browser reaches Vite on the configured PC address. FastAPI stays on
`127.0.0.1:8000` and uses the configured read-only database. The proxy serves
the macro catalog, the five histories and the optional research POST. The
shared token travels only over loopback from Vite to FastAPI. The LAN HTTP
connection carries the selected dates, observations and research questions,
so use it on a trusted private network. If the iPhone's IP changes, restart
Vite with the new address.

For simultaneous PC access, start another Vite process from `frontend/` in
a regular PowerShell window, set `MARKETPULSE_MACRO_AI_ENABLED=true` if you
want RAG on the PC, and open `http://127.0.0.1:5173/`. The two Vite processes
use distinct local addresses.

To disable LAN mode, press **Ctrl+C** in the LAN Vite window and close it.
Stop and close the FastAPI window when finished. If you created the temporary
firewall rule, remove it from an administrator PowerShell window:

```powershell
Remove-NetFirewallRule -Name 'MarketPulse-LAN-Dev-5173'
```

The launcher passes the LAN settings only to the two child windows. If you
set any LAN variables directly in a shell, clear them there before restarting
in normal loopback mode:

```powershell
Remove-Item Env:MARKETPULSE_LAN_DEV,Env:MARKETPULSE_LAN_IP,Env:MARKETPULSE_LAN_DEVICE_IP,Env:MARKETPULSE_LAN_TOKEN -ErrorAction SilentlyContinue
```

## Contrato y decisiones

- React 19.3.1 con TypeScript 6.0.3 estricto y Vite 8.3.7. El estado de catálogo, gráfico y análisis vive en React. Las solicitudes se cancelan al desmontar o cambiar su ámbito.
- uPlot 1.6.32 usa canvas y un eje por observación. Cada posición corresponde a una fecha real en UTC y a un precio válido. El gráfico conecta las observaciones consecutivas y el selector de fecha distingue las sesiones ausentes de los registros sin precio para la serie elegida. Se conserva el `Decimal` de FastAPI, se usa `Number` para dibujar y se muestra un decimal en la interfaz. El frontend presenta las variaciones de precio que devuelve el análisis. [Versiones de uPlot](https://github.com/leeoniya/uPlot/releases)
- Los `GET /assets` y `GET /assets/{asset_source_id}/history` usan los identificadores devueltos por el catálogo. La vista inicial usa Oro si está disponible, con un año de Close. Las ventanas terminan en el día posterior a `last_date`, exclusivo. Su inicio resta meses calendario con ajuste al último día del mes y se recorta a la cobertura del activo. Máximo usa toda la cobertura.
- `POST /analysis` recibe el `asset_id` lógico, un acontecimiento admitido, su ventana histórica y una pregunta de hasta 1024 caracteres y 48 términos. Una pregunta personalizada usa el mismo acontecimiento y ventana. El análisis es retrospectivo y el formulario usa el alcance predeterminado del backend para `as_of_exclusive`. La API informa el proveedor simulado y distingue cobertura directa, parcial e insuficiente.
- El navegador usa rutas `/api` del mismo origen. Vite expone un proxy local acotado a catálogo, histórico y análisis. Las credenciales y la conexión a DEV se configuran en FastAPI. El build produce archivos estáticos y su integración requiere mantener esas rutas del mismo origen.
- Las versiones y licencias se verificaron en los metadatos del registro npm. React, React DOM, Vite y uPlot declaran MIT. TypeScript declara Apache-2.0. Vite genera las licencias de las dependencias empaquetadas en `dist/.vite/license.md`.

## Verificación local

Desde `frontend/` con las dependencias instaladas:

```powershell
npm.cmd run typecheck
npm.cmd run lint
npm.cmd test
npm.cmd run build
npm.cmd audit
```

1. Confirmá que se cargan los seis activos del catálogo DEV. Probá **Gold** y **Bitcoin**, y verificá que cambian color, unidad, fechas y pregunta predefinida.
2. Elegí **Crude Oil**, **Max** y **20/04/2020** en **Inspect date**. Comprobá el cierre de **-37.6 USD/bbl**. Seleccioná **19/04/2020** para revisar un día sin observación y probá el selector de **Adj Close**.
3. Ejecutá una consulta predefinida y una personalizada. Revisá el período de análisis, el indicador de proveedor, la cobertura y los enlaces originales cuando haya citas.
4. Probá en una ventana angosta y navegá con teclado. Para comprobar el estado de error, detené FastAPI, cambiá de período, reiniciá FastAPI y usá **Retry**.

## Production PWA on Windows

From `frontend/`, build and serve the production files on loopback:

```powershell
npm.cmd ci
npm.cmd run build
npm.cmd exec vite -- preview --host 127.0.0.1 --port 4173 --strictPort
```

Open `http://127.0.0.1:4173/` in a current desktop browser. Vite preview is
for local build verification. It binds to loopback by default and has no API
proxy. The regular Vite development commands above still serve the manifest
and icons without registering a worker. Use a same-origin synthetic API
fixture server to verify the online catalog, charts, and analysis during a
local build test.

1. In the browser's Application panel, verify the manifest has standalone
   display and the worker `sw.js` has activated. Reload once to put the page
   under worker control. If registration fails, check the console and confirm
   the worker and all precache files came from the same build.
2. Inspect Cache Storage. The scope-specific `marketpulse-shell:` cache should
   contain the build's index, manifest, icons, and hashed CSS/JavaScript.
   After using the catalog, history, or analysis, verify those routes are
   absent from Cache Storage. The application's API requests also use
   `cache: 'no-store'` to bypass the browser's HTTP cache.
3. Go offline using browser network controls. Reload the app's root. The
   shell should show **You are offline. Live financial data is unavailable.**
   Restore the connection and check that the app requests fresh data.
4. To exercise an update, serve a second build with a distinguishable static
   change on the same preview origin. In the original tab, trigger a worker
   update check from the Application panel. It should install and wait while
   the first build has open clients. After the update notice appears, close
   all app tabs and windows and reopen the app. Confirm the second build is
   active and the old shell cache is gone. Leave unrelated origin caches
   untouched.

Private LAN HTTP can serve the frontend, icons, and manifest, and iPhone
Safari can add the page to the Home Screen. A private LAN IP over HTTP is not
a secure context. Service worker registration, offline relaunch, and worker
updates on a physical iPhone must be verified on trusted HTTPS.

### Phase 4.4 hosting requirements

- Publish the complete contents of `frontend/dist/` to a trusted HTTPS static
  origin, preferably at its root. Keep the worker at the app root and preserve
  the generated directory structure. If mounting under a path, use its
  trailing slash for the app URL and keep the browser's `/api` endpoint at the
  origin root.
- Serve `sw.js` as JavaScript and the manifest as
  `application/manifest+json`. Revalidate `sw.js`, the entry HTML, manifest,
  and unversioned icons. Hashed assets can use immutable caching. Serve the
  static shell to anonymous clients using bytes from one build. Compression
  is compatible, but HTML or asset rewriting breaks precache integrity.
- Deploy the new hashed files before exposing the new worker and entry HTML,
  or publish the complete release atomically. Keep the previous release's
  hashed assets available during rollout for pages opened before the worker
  took control. Verify install, offline relaunch, and update activation on
  a real iPhone and on the supported desktop browsers.
- Route only the required `/api` operations to the backend on the same origin.
  Keep private data and credentials on the server. Define access controls,
  disable intermediary caching for API responses, and verify the HTTPS
  proxy's allowed methods and routes before public rollout.

Vite 8 is supported by vite-plugin-pwa 1.3.0. The Vite build generates a
worker directly because this shell needs only an integrity-pinned list of
build outputs. [vite-plugin-pwa release notes](https://github.com/vite-pwa/vite-plugin-pwa/releases/tag/v1.3.0)

Each release has an exact integrity-pinned static allowlist. The worker requests
those files without credentials, serves cached shell content at the app root
and `index.html`, and removes only obsolete shell caches in its own scope.
API calls, financial documents, and cross-origin resources use the network
without service-worker caching.
