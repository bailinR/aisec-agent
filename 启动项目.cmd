@echo off
setlocal

cd /d "%~dp0"

echo Starting aisec-agent Web + worker...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-local.ps1" -Restart -Open

echo.
echo Done. You can close this window after checking the startup output.
pause
