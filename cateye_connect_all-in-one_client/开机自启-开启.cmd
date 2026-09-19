@echo off
chcp 65001 >nul
title Cateye Connect 开机自启动
cd /d "%~dp0"

set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

echo 正在开启开机自启动...
"%PYTHON%" main.py --autostart on
echo.
echo 说明：已在本用户启动文件夹创建快捷方式（最小化窗口启动）。
echo 取消请运行 开机自启-关闭.cmd
pause
