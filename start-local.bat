@echo off
chcp 65001 >nul
title gcli2api - Local Mode (No Git Pull)

echo ============================================
echo   gcli2api Local Startup
echo   (Skipping git pull to preserve local changes)
echo ============================================
echo.

cd /d "%~dp0"

echo Activating virtual environment...
call .venv\Scripts\activate.bat

echo Starting web server...
python web.py

pause
