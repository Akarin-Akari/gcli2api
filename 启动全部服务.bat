@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
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
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment activation script not found!
    echo [ERROR] Please run: uv venv && uv sync
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment!
    pause
    exit /b 1
)

echo [INFO] Verifying Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found or not in PATH!
    pause
    exit /b 1
)

echo [INFO] Checking web.py file...
if not exist "web.py" (
    echo [ERROR] web.py not found in current directory!
    pause
    exit /b 1
)

echo [INFO] Testing imports...
python -c "import sys; sys.path.insert(0, '.'); from src.antigravity_api import *" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Import test failed, but continuing anyway...
    echo [WARN] This might indicate a code issue. Check the error above.
    timeout /t 2 /nobreak >nul
)

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
if errorlevel 1 (
    echo.
    echo [ERROR] Web server exited with error code %errorlevel%
    echo [ERROR] Check the error messages above for details.
    echo.
    echo [TROUBLESHOOTING] If you see PermissionError [WinError 10013]:
    echo   1. Check if port 7861 is in Windows reserved port range
    echo   2. Run: netsh int ipv4 show excludedportrange protocol=tcp
    echo   3. Try running this script as Administrator
    echo   4. Or change the port in config.py
    echo.
)

pause
