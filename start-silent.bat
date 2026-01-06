@echo off
:: gcli2api 静默启动器
:: 使用 VBScript 实现完全静默启动（无窗口）
:: 作者：幽浮喵 (浮浮酱)
:: 日期：2025-12-22

cd /d "%~dp0"
wscript.exe "start-silent.vbs"
