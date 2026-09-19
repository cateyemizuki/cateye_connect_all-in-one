@echo off
chcp 65001 >nul
title Cateye Connect 开机自启动
cd /d "%~dp0"

set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

"%PYTHON%" main.py --autostart off
pause
