@echo off
chcp 65001 >nul
title Antigravity2API Services (Official Branch)

echo ========================================
echo   Antigravity2API Service Launcher
echo   (Official Branch - Local Mode)
echo ========================================
echo.

cd /d "%~dp0"

echo [INFO] Checking and killing processes on required ports...

REM Check and kill process on port 7861
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7861 ^| findstr LISTENING') do (
    echo [INFO] Killing process on port 7861 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

REM Check and kill process on port 8141
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8141 ^| findstr LISTENING') do (
    echo [INFO] Killing process on port 8141 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo [INFO] Stopping existing bun processes...
start /b "" cmd /c "taskkill /F /IM bun.exe >nul 2>&1"
ping -n 2 127.0.0.1 >nul

timeout /t 2 /nobreak >nul

echo.
echo Starting services:
echo   1. copilot-api (port 8141)
echo   2. gcli2api-official (port 7861)
echo.
echo ========================================
echo.

set COPILOT_DIR=
if exist "%~dp0..\copilot-api" set COPILOT_DIR=%~dp0..\copilot-api
if exist "%~dp0copilot-api" set COPILOT_DIR=%~dp0copilot-api

if not exist "%USERPROFILE%\.local\share\copilot-api\github_token" (
    echo [WARN] Copilot API not authenticated, skipping...
) else if "%COPILOT_DIR%"=="" (
    echo [WARN] copilot-api folder not found, skipping...
) else (
    echo [INFO] Starting copilot-api on port 8141...
    start "Copilot API" cmd /k "cd /d %COPILOT_DIR% && %USERPROFILE%\.bun\bin\bun.exe run ./src/main.ts start --port 8141 --verbose"
)

echo [INFO] Waiting 3 seconds...
timeout /t 3 /nobreak >nul

echo [INFO] Starting gcli2api-official on port 7861...
echo.

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
echo   copilot-api (8141):
echo     http://127.0.0.1:8141/v1
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
