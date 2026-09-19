"""开机自启动管理（Windows）。

在当前用户的启动文件夹（%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup）
创建/移除指向 启动客户端.cmd 的快捷方式（最小化窗口启动）。

入口：
  python main.py --autostart on|off|status
  框架根目录的 开机自启-开启.cmd / 开机自启-关闭.cmd
  WebUI 状态页的「开机自启动」开关
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = CLIENT_ROOT / "启动客户端.cmd"
SHORTCUT_NAME = "Cateye Connect 本地端.lnk"
DESCRIPTION = "Cateye Connect 本地端（开机自启动）"


def default_startup_dir() -> Path:
    """当前用户的启动文件夹。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("未找到 APPDATA 环境变量，此功能仅支持 Windows")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path(startup: Path | None = None) -> Path:
    return (startup or default_startup_dir()) / SHORTCUT_NAME


def is_enabled(startup: Path | None = None) -> bool:
    return shortcut_path(startup).is_file()


def _ps_quote(value: str) -> str:
    """PowerShell 单引号字符串转义。"""
    return "'" + value.replace("'", "''") + "'"


def _run_ps(script: str) -> str:
    # 显式让 PowerShell 用 UTF-8 写 stdout，避免中文输出按 GBK 编码
    prefixed = (
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        + script
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", prefixed],
        capture_output=True,
        timeout=30,
    )
    out = proc.stdout or b""
    err = proc.stderr or b""
    text = _decode(out)
    if proc.returncode != 0:
        err_text = _decode(err).strip() or "PowerShell 执行失败"
        raise RuntimeError(err_text)
    return text


def _decode(data: bytes) -> str:
    """PowerShell 输出解码：优先 UTF-8（脚本已指定），回退 GBK。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def install(startup: Path | None = None) -> Path:
    """创建开机自启动快捷方式，返回快捷方式路径。"""
    if os.name != "nt":
        raise RuntimeError("开机自启动仅支持 Windows")
    if not LAUNCHER.is_file():
        raise FileNotFoundError(f"未找到启动脚本: {LAUNCHER}")
    target = shortcut_path(startup)
    target.parent.mkdir(parents=True, exist_ok=True)
    script = "\n".join(
        [
            "$sh = New-Object -ComObject WScript.Shell",
            f"$lnk = $sh.CreateShortcut({_ps_quote(str(target))})",
            f"$lnk.TargetPath = {_ps_quote(str(LAUNCHER))}",
            f"$lnk.WorkingDirectory = {_ps_quote(str(CLIENT_ROOT))}",
            "$lnk.WindowStyle = 7",  # 最小化启动
            f"$lnk.Description = {_ps_quote(DESCRIPTION)}",
            "$lnk.Save()",
        ]
    )
    _run_ps(script)
    if not target.is_file():
        raise RuntimeError("快捷方式创建失败（未知原因）")
    return target


def uninstall(startup: Path | None = None) -> bool:
    """移除开机自启动快捷方式。返回是否存在过。"""
    target = shortcut_path(startup)
    if target.is_file():
        target.unlink()
        return True
    return False
