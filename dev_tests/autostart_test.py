#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""autostart 模块测试：在临时目录模拟启动文件夹，不触碰真实开机自启。

覆盖：install 创建快捷方式（PowerShell 回读校验目标路径）、is_enabled、uninstall。
另验证 main.py --autostart status 只读可用。

用法：python autostart_test.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT / "cateye_connect_all-in-one_client"
sys.path.insert(0, str(CLIENT_DIR))

from framework import autostart  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)

        check("初始未开启", autostart.is_enabled(startup) is False)
        check("LAUNCHER 存在", autostart.LAUNCHER.is_file(), str(autostart.LAUNCHER))

        target = autostart.install(startup)
        check("install 创建快捷方式", target.is_file(), str(target))
        check("install 后 is_enabled", autostart.is_enabled(startup) is True)

        # PowerShell 回读快捷方式，校验目标与工作目录（显式 UTF-8 输出避免 GBK 乱码）
        ps = (
            "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$sh = New-Object -ComObject WScript.Shell; "
            f"$lnk = $sh.CreateShortcut('{target}'); "
            "Write-Output $lnk.TargetPath; Write-Output $lnk.WorkingDirectory; Write-Output $lnk.WindowStyle"
        )
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=30,
        )
        lines = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
        check("快捷方式指向 启动客户端.cmd",
              len(lines) >= 1 and Path(lines[0]) == autostart.LAUNCHER, str(lines))
        check("快捷方式工作目录为框架根",
              len(lines) >= 2 and Path(lines[1]) == CLIENT_DIR, str(lines))
        check("最小化窗口启动", len(lines) >= 3 and lines[2].strip() == "7", str(lines))

        removed = autostart.uninstall(startup)
        check("uninstall 移除快捷方式", removed is True and autostart.is_enabled(startup) is False)
        check("重复 uninstall 返回 False", autostart.uninstall(startup) is False)

    # CLI 只读状态
    proc = subprocess.run(
        [sys.executable, "main.py", "--autostart", "status"],
        cwd=str(CLIENT_DIR), capture_output=True, timeout=30,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    check("main.py --autostart status 可用", proc.returncode == 0 and "开机自启动" in out,
          out.strip()[:120])

    print()
    if FAILURES:
        print(f"autostart 测试失败: {FAILURES}")
        sys.exit(1)
    print("autostart 测试全部通过")


if __name__ == "__main__":
    main()
