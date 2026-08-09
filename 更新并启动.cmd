@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update-and-start.ps1"
if errorlevel 1 (
  echo.
  echo Update or startup failed. Review the message above.
  pause
  exit /b 1
)

echo.
echo Update and startup completed.
pause
