@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Starting aisec-agent (first-time install or update + single instance)...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Review the message above and runtime\logs.
  pause
  exit /b 1
)

echo.
echo Done. Web should be available at http://127.0.0.1:7860
pause
