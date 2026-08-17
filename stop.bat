@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Stopping aisec-agent on port 7860...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
if errorlevel 1 (
  echo.
  echo Stop failed. Review the message above.
  pause
  exit /b 1
)

echo.
echo Stop completed.
pause
