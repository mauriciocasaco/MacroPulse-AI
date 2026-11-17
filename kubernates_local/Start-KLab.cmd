@echo off
cd /d "%~dp0"
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-KLab.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-KLab.ps1"
)
if errorlevel 1 echo [ERROR] Startup incomplete. See messages above.
pause
