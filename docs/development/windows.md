# Windows development

Use this guide from a local MacroPulse AI checkout. See the [main development guide](../../README.md#local-development) for the manual loopback workflow.

## PowerShell launcher and private LAN

Run the PowerShell launcher from the repository root on Windows 11. It opens the browser at `http://127.0.0.1:5173/` and displays the iPhone Safari URL. Connect both devices to the same trusted network and select the **Private** profile on the PC.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\dev-all.ps1 -Action Start
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\dev-all.ps1 -Action Stop
```

Run the same command with `-Action Install` to create or update the **MacroPulse AI** Start menu shortcut. It runs the PowerShell launcher directly. Run it again with `-Action Start` to open the running app. Stop and restart after editing frontend source or changing networks.

If Windows Firewall blocks Safari, run the command with `-Action Firewall` and approve the Windows elevation prompt. It allows Node on TCP 5173 for the selected interface, Private profile, and local subnet. Run it again after changing adapters or the Node installation path.

The launcher uses the existing DEV read-only settings and Gemini key from the backend environment or ignored root `.env`. FastAPI listens on `127.0.0.1:8000`, and both frontend listeners serve the same compiled app. The LAN proxy accepts only peers on the selected private subnet and allows up to 60 history requests and three research submissions per rolling minute across the LAN listener. LAN access uses HTTP, so use it only on a trusted private network.

If setup is missing, install Python 3.12 and a Node release supported by [frontend/package.json](../../frontend/package.json), then install the repository dependencies once from PowerShell at the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt -r backend/requirements-dev.txt
Set-Location frontend
npm.cmd ci
```

The launcher reports startup errors in its window. The latest supervisor status is in `status.txt` under `%LOCALAPPDATA%\MacroPulse AI\` in the checkout's subdirectory. For hot reload and manual development, see the [frontend guide](../../frontend/README.md).

## Troubleshooting and verification

- If the launcher reports a startup failure, check the supervisor status file and its startup output before retrying. If the frontend or network configuration changed, stop the services before launching again.
- For manual Vite development, see the [frontend startup guide](../../frontend/README.md#inicio-en-windows). To test Safari with a device-scoped firewall rule and stop the listeners manually afterwards, follow the [private LAN walkthrough](../../frontend/README.md#iphone-testing-on-a-private-lan).
- See the [local test commands](../../README.md#run-the-tests-locally) and the [disposable PostgreSQL workflow](../market-data-postgres.md#local-tests). For macro database integration tests, point `MACRO_DATA_TEST_DSN` and `MARKET_DATA_TEST_DSN` at the same disposable loopback instance.

From `frontend`, use the browser suite, lint, and both PWA builds for further local verification:

```powershell
npm.cmd run test:browser
npm.cmd run lint
npm.cmd run build
npm.cmd run build:public
```
