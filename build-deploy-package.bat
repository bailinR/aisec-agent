@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo aisec-agent 一键部署包构建
echo.
echo  1 = 轻量包 lite   （体积小，目标机首次启动联网安装运行时）
echo  2 = 完整包 full   （体积大，内置 Python/Redis/Chromium，开箱即用）
echo.
set /p MODE_CHOICE=请选择 [1/2，默认 1]: 
if "%MODE_CHOICE%"=="" set MODE_CHOICE=1
if "%MODE_CHOICE%"=="2" (
  set BUILD_MODE=full
) else (
  set BUILD_MODE=lite
)

echo.
echo 正在构建 %BUILD_MODE% 包...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-portable-package.ps1" -Mode %BUILD_MODE%
if errorlevel 1 (
  echo.
  echo 构建失败，请查看上方报错。
  pause
  exit /b 1
)

echo.
echo 构建完成。部署包位于 .deploy_build 目录。
echo 将整个 zip 发给其他电脑，解压后双击 start.bat 即可。
echo.
pause
