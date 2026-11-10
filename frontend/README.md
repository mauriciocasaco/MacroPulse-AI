# MarketPulse AI · Frontend local

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
Start-Process 'http://127.0.0.1:5173'
```

Vite usa el puerto fijo `5173` en loopback. Si el puerto `8000` ya está ocupado, iniciá FastAPI con `--port 8001` y ejecutá `$env:MARKETPULSE_API_PORT = '8001'` en la terminal B antes de iniciar Vite. Para detener ambos procesos, presioná **Ctrl+C** en cada terminal. Si usaste un puerto alternativo, podés limpiar esa variable con `Remove-Item Env:MARKETPULSE_API_PORT`.

## iPhone testing on a private LAN

Connect the iPhone to the Wi-Fi on the same subnet as the PC. The PC can use
Ethernet. Use a trusted private network and close the LAN listener when done.

Start FastAPI from the repository root using the existing read-only DEV configuration.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8001
```

Start the LAN listener from a second terminal at the repository root. Use the
active private interface that leads to the iPhone. Change `Ethernet` to the
actual adapter name if needed.

```powershell
$env:MARKETPULSE_LAN_IP = (Get-NetIPConfiguration -InterfaceAlias 'Ethernet').IPv4Address.IPAddress
$env:MARKETPULSE_API_PORT = '8001'
Set-Location frontend
Write-Host "http://$($env:MARKETPULSE_LAN_IP):5173/"
npm.cmd run dev
```

For simultaneous desktop access, keep the regular loopback Vite instance
running in its own terminal with `MARKETPULSE_API_PORT=8001` and without
`MARKETPULSE_LAN_IP`. Both listeners use port 5173 on distinct addresses.
Run `Invoke-RestMethod http://127.0.0.1:8001/health` and check `/api/assets` on
the Vite URL to distinguish FastAPI liveness from catalog connectivity. Stop
each listener you started with **Ctrl+C** in its terminal.

The LAN listener accepts requests for its configured IP from its own subnet.
It serves the development frontend and proxies only catalog, history, and
simulated analysis. FastAPI's diagnostic routes stay on loopback. Use Safari
to check portrait and landscape layout, tap and slide on the chart, and
scroll vertically across it. Check **Crude Oil** on **Max** using **Inspect date**
for April 20, 2020. The reading should be **-37.6 USD/bbl**. Then test a preset,
a custom question, and a document link.

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
