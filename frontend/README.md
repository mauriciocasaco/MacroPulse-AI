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
scroll vertically across it. Check WTI on **Max** using **Inspect date** for
April 20, 2020, then test a preset, a custom question, and a document link.

## Contrato y decisiones

- React 19.3.1 con TypeScript 6.0.3 estricto y Vite 8.3.7. El estado de catálogo, gráfico y análisis vive en React. Las solicitudes se cancelan al desmontar o cambiar su ámbito.
- uPlot 1.6.32 usa canvas y un eje de tiempo UTC. Las fechas intermedias sin observación ocupan espacio con `null` y dejan un hueco. El valor exacto se muestra desde el `Decimal` original de FastAPI. `Number` se usa para dibujar. El frontend presenta únicamente las variaciones de precio que devuelve el análisis. [Versiones de uPlot](https://github.com/leeoniya/uPlot/releases)
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

1. Confirmá que se cargan los seis activos del catálogo DEV. Probá Oro y Bitcoin, y verificá que cambian unidad, fechas y pregunta predefinida.
2. Elegí Petróleo WTI, **Máximo** y **20/04/2020** en **Inspeccionar fecha**. Comprobá el cierre negativo. Seleccioná **19/04/2020** para revisar un día sin observación y probá el selector de **Adj Close**.
3. Ejecutá una consulta predefinida y una personalizada. Revisá el período de análisis, el indicador de proveedor, la cobertura y los enlaces originales cuando haya citas.
4. Probá en una ventana angosta y navegá con teclado. Para comprobar el estado de error, detené FastAPI, cambiá de período, reiniciá FastAPI y usá **Reintentar**.
