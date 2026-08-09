@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-portable.ps1"
if errorlevel 1 (
  echo.
  echo Stop failed.
  pause
  exit /b 1
)

pause
