@echo off
chcp 65001 >nul
title gcli2api 开机自启动配置

echo ============================================
echo   gcli2api 开机自启动配置工具
echo   作者：幽浮喵 (浮浮酱)
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "VBS_PATH=%SCRIPT_DIR%start-silent.vbs"

echo 请选择配置方式：
echo.
echo [1] 添加到任务计划程序（推荐，完全静默）
echo [2] 添加到启动目录（使用 VBS 静默启动）
echo [3] 移除开机自启动
echo [4] 退出
echo.

set /p choice="请输入选项 (1-4): "

if "%choice%"=="1" goto :task_scheduler
if "%choice%"=="2" goto :startup_folder
if "%choice%"=="3" goto :remove_autostart
if "%choice%"=="4" goto :exit

echo 无效选项，请重新运行
pause
exit /b

:task_scheduler
echo.
echo 正在配置任务计划程序...

:: 删除已存在的任务（如果有）
schtasks /delete /tn "gcli2api-autostart" /f >nul 2>&1

:: 创建新任务 - 用户登录时运行
schtasks /create /tn "gcli2api-autostart" /tr "wscript.exe \"%VBS_PATH%\"" /sc onlogon /rl highest /f

if %errorlevel%==0 (
    echo.
    echo ✓ 任务计划程序配置成功！
    echo.
    echo 任务名称：gcli2api-autostart
    echo 触发条件：用户登录时
    echo 运行方式：完全静默（无窗口）
    echo.
    echo 下次登录 Windows 时，gcli2api 和 ngrok 将自动静默启动。
) else (
    echo.
    echo ✗ 配置失败，请尝试以管理员身份运行此脚本
)
goto :end

:startup_folder
echo.
echo 正在添加到启动目录...

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\gcli2api-silent.lnk"

:: 使用 PowerShell 创建快捷方式
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%VBS_PATH%\"'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.WindowStyle = 7; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo ✓ 启动目录配置成功！
    echo.
    echo 快捷方式位置：%SHORTCUT_PATH%
    echo 运行方式：静默启动（使用 VBS）
    echo.
    echo 下次登录 Windows 时，gcli2api 和 ngrok 将自动静默启动。
) else (
    echo.
    echo ✗ 配置失败
)
goto :end

:remove_autostart
echo.
echo 正在移除开机自启动...

:: 移除任务计划程序任务
schtasks /delete /tn "gcli2api-autostart" /f >nul 2>&1
echo ✓ 已移除任务计划程序任务

:: 移除启动目录快捷方式
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
del "%STARTUP_FOLDER%\gcli2api-silent.lnk" >nul 2>&1
echo ✓ 已移除启动目录快捷方式

echo.
echo 开机自启动已完全移除。
goto :end

:end
echo.
pause
exit /b

:exit
exit /b
