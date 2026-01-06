@echo off
chcp 65001 >nul
title gcli2api 日志查看器

:menu
cls
echo ============================================
echo   gcli2api 日志查看器
echo   作者：幽浮喵 (浮浮酱)
echo ============================================
echo.

set "LOG_DIR=%~dp0logs"
set "TODAY=%date:~0,4%-%date:~5,2%-%date:~8,2%"

echo 日志目录: %LOG_DIR%
echo 今日日期: %TODAY%
echo.
echo ============================================
echo.

if not exist "%LOG_DIR%" (
    echo [!] 日志目录不存在，可能还没有运行过静默启动
    echo.
    pause
    exit /b
)

echo 请选择操作：
echo.
echo [1] 查看今日 gcli2api 日志
echo [2] 查看今日 ngrok 日志
echo [3] 实时跟踪 gcli2api 日志 (tail -f)
echo [4] 实时跟踪 ngrok 日志 (tail -f)
echo [5] 查看所有日志文件
echo [6] 清理旧日志 (保留最近7天)
echo [7] 打开日志目录
echo [8] 获取 ngrok 公网 URL
echo [9] 退出
echo.

set /p choice="请输入选项 (1-9): "

if "%choice%"=="1" goto :view_gcli
if "%choice%"=="2" goto :view_ngrok
if "%choice%"=="3" goto :tail_gcli
if "%choice%"=="4" goto :tail_ngrok
if "%choice%"=="5" goto :list_logs
if "%choice%"=="6" goto :clean_logs
if "%choice%"=="7" goto :open_dir
if "%choice%"=="8" goto :get_ngrok_url
if "%choice%"=="9" exit /b

echo 无效选项
timeout /t 2 >nul
goto :menu

:view_gcli
cls
echo ============================================
echo   gcli2api 日志 - %TODAY%
echo ============================================
echo.
if exist "%LOG_DIR%\gcli2api_%TODAY%.log" (
    type "%LOG_DIR%\gcli2api_%TODAY%.log"
) else (
    echo [!] 今日日志文件不存在
)
echo.
echo ============================================
pause
goto :menu

:view_ngrok
cls
echo ============================================
echo   ngrok 日志 - %TODAY%
echo ============================================
echo.
if exist "%LOG_DIR%\ngrok_%TODAY%.log" (
    type "%LOG_DIR%\ngrok_%TODAY%.log"
) else (
    echo [!] 今日日志文件不存在
)
echo.
echo ============================================
pause
goto :menu

:tail_gcli
cls
echo ============================================
echo   实时跟踪 gcli2api 日志 (按 Ctrl+C 退出)
echo ============================================
echo.
if exist "%LOG_DIR%\gcli2api_%TODAY%.log" (
    powershell -Command "Get-Content '%LOG_DIR%\gcli2api_%TODAY%.log' -Wait -Tail 50"
) else (
    echo [!] 今日日志文件不存在
    pause
)
goto :menu

:tail_ngrok
cls
echo ============================================
echo   实时跟踪 ngrok 日志 (按 Ctrl+C 退出)
echo ============================================
echo.
if exist "%LOG_DIR%\ngrok_%TODAY%.log" (
    powershell -Command "Get-Content '%LOG_DIR%\ngrok_%TODAY%.log' -Wait -Tail 50"
) else (
    echo [!] 今日日志文件不存在
    pause
)
goto :menu

:list_logs
cls
echo ============================================
echo   所有日志文件
echo ============================================
echo.
dir /b /o-d "%LOG_DIR%\*.log" 2>nul
if %errorlevel% neq 0 (
    echo [!] 没有找到日志文件
)
echo.
pause
goto :menu

:clean_logs
cls
echo ============================================
echo   清理旧日志 (保留最近7天)
echo ============================================
echo.
forfiles /p "%LOG_DIR%" /m *.log /d -7 /c "cmd /c del @path && echo 已删除: @file" 2>nul
if %errorlevel% neq 0 (
    echo [i] 没有需要清理的旧日志
)
echo.
echo 清理完成！
pause
goto :menu

:open_dir
explorer "%LOG_DIR%"
goto :menu

:get_ngrok_url
cls
echo ============================================
echo   获取 ngrok 公网 URL
echo ============================================
echo.
echo 正在查询 ngrok API...
echo.

curl -s http://127.0.0.1:4040/api/tunnels 2>nul | findstr /i "public_url" >nul
if %errorlevel%==0 (
    echo ngrok 隧道信息：
    echo.
    curl -s http://127.0.0.1:4040/api/tunnels 2>nul
    echo.
    echo.
    echo 提示：复制上面的 public_url 值配置到 Cursor
) else (
    echo [!] 无法连接到 ngrok API
    echo     可能 ngrok 未运行或未正确启动
    echo.
    echo 尝试从日志中查找 URL...
    echo.
    if exist "%LOG_DIR%\ngrok_%TODAY%.log" (
        findstr /i "url=" "%LOG_DIR%\ngrok_%TODAY%.log" 2>nul
        findstr /i "Forwarding" "%LOG_DIR%\ngrok_%TODAY%.log" 2>nul
    )
)
echo.
pause
goto :menu
