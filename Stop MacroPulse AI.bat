@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0scripts\dev-all.ps1" -Action Stop
if errorlevel 1 pause
