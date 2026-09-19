@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Cateye Connect 本地端框架

echo ========================================
echo   Cateye Connect - 本地端框架
echo ========================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [X] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

if not exist "config.json" (
    echo [!] 未找到 config.json
    echo     正在从 config.example.json 复制模板...
    copy "config.example.json" "config.json" >nul
    echo [OK] 已创建 config.json，请编辑填入 ws_url/token
    echo     文件位置: !cd!\config.json
    pause
    exit /b 0
)

set VENV_DIR=!cd!\.venv
if not exist "!VENV_DIR!\Scripts\python.exe" (
    echo [>] 创建虚拟环境...
    python -m venv "!VENV_DIR!"
    if !errorlevel! neq 0 (
        echo [X] 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo [OK] 虚拟环境已创建
)

set PYTHON=!VENV_DIR!\Scripts\python.exe
set PIP=!VENV_DIR!\Scripts\pip.exe

echo [>] 检查依赖包...
set NEED_INSTALL=0

"%PYTHON%" -c "import websockets" 2>nul || set NEED_INSTALL=1
"%PYTHON%" -c "import PIL" 2>nul || set NEED_INSTALL=1
"%PYTHON%" -c "import aiohttp" 2>nul || set NEED_INSTALL=1

if !NEED_INSTALL! equ 1 (
    echo [>] 正在安装依赖: websockets pillow
    "%PIP%" install -r requirements.txt -i https://pypi.douban.com/simple/
    if !errorlevel! neq 0 (
        echo [!] 豆瓣镜像失败，尝试默认源...
        "%PIP%" install -r requirements.txt
        if !errorlevel! neq 0 (
            echo [X] 依赖安装失败，请手动执行:
            echo      !VENV_DIR!\Scripts\pip.exe install -r requirements.txt
            pause
            exit /b 1
        )
    )
    echo [OK] 依赖安装完成
)

echo [>] 启动框架...
echo.

"%PYTHON%" main.py

pause