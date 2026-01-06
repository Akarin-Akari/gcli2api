@echo off
chcp 65001 >nul
title 停止 gcli2api 服务

echo ============================================
echo   停止 gcli2api 和 ngrok 服务
echo ============================================
echo.

echo 正在停止 Python 进程 (gcli2api)...
taskkill /f /im python.exe >nul 2>&1
if %errorlevel%==0 (
    echo ✓ Python 进程已停止
) else (
    echo - Python 进程未运行
)

echo.
echo 正在停止 ngrok 进程...
taskkill /f /im ngrok.exe >nul 2>&1
if %errorlevel%==0 (
    echo ✓ ngrok 进程已停止
) else (
    echo - ngrok 进程未运行
)

echo.
echo ============================================
echo   所有服务已停止
echo ============================================
echo.
pause
