@echo off
chcp 65001 >nul
title Antigravity2API Services (Official Branch)

echo ========================================
echo   Antigravity2API Service Launcher
echo   (Official Branch - Local Mode)
echo ========================================
echo.

cd /d "%~dp0"

echo [INFO] Stopping existing bun processes...
REM 使用 start /b 在后台执行 taskkill，避免卡住
start /b "" cmd /c "taskkill /F /IM bun.exe >nul 2>&1"
REM 短暂等待确保命令已发出
ping -n 2 127.0.0.1 >nul

timeout /t 2 /nobreak >nul

echo.
echo Starting services:
echo   1. copilot-api (port 4141)
echo   2. gcli2api-official (port 7861)
echo.
echo ========================================
echo.

REM 检查 copilot-api 目录（在父目录或同级目录）
set COPILOT_DIR=
if exist "%~dp0..\copilot-api" (
    set COPILOT_DIR=%~dp0..\copilot-api
) else if exist "%~dp0copilot-api" (
    set COPILOT_DIR=%~dp0copilot-api
)

if not exist "%USERPROFILE%\.local\share\copilot-api\github_token" (
    echo [WARN] Copilot API not authenticated, skipping...
) else if "%COPILOT_DIR%"=="" (
    echo [WARN] copilot-api folder not found, skipping...
) else (
    echo [INFO] Starting copilot-api on port 4141...
    start "Copilot API" cmd /k "cd /d %COPILOT_DIR% && %USERPROFILE%\.bun\bin\bun.exe run ./src/main.ts start --port 4141 --verbose"
)

echo [INFO] Waiting 3 seconds...
timeout /t 3 /nobreak >nul

echo [INFO] Starting gcli2api-official on port 7861...
echo.

REM 检查虚拟环境是否存在
if not exist ".venv\Scripts\activate.bat" (
    echo [WARN] Virtual environment not found, creating...
    uv venv
    echo [INFO] Installing dependencies...
    uv sync
)

echo Activating virtual environment...
call .venv\Scripts\activate.bat

echo Starting web server...
echo.
echo ========================================
echo   Endpoints:
echo ========================================
echo.
echo   gcli2api-official (7861):
echo     http://127.0.0.1:7861
echo     http://127.0.0.1:7861/v1
echo     http://127.0.0.1:7861/antigravity/v1
echo.
echo   copilot-api (4141):
echo     http://127.0.0.1:4141/v1
echo.
echo   Gateway (7861) - USE THIS:
echo     http://127.0.0.1:7861/gateway/v1
echo.
echo ========================================
echo.
echo [NOTE] This script does NOT pull from remote.
echo        Local modifications are preserved.
echo.

python web.py

pause

