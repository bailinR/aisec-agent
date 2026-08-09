@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy-start.ps1" -Restart -Open
if errorlevel 1 (
  echo.
  echo Deployment or startup failed. Review the message above and runtime\logs.
  pause
  exit /b 1
)

echo.
echo Deployment and startup completed.
pause
