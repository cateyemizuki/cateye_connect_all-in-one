#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WebUI 端到端测试：启动真实框架（含 WebUI），用 HTTP 客户端验证全流程。

覆盖：
  1. 未登录访问 → 401；错误密码 → 401；正确密码 → Cookie 登录
  2. /api/status：插件列表与连接状态
  3. /api/config/framework：读取 schema+config；保存修改 → 写盘生效
  4. /api/config/plugin/<name>：读取 schema（plugin.json 声明）；保存 → 写盘 + 热更新
  5. /api/action/reconnect

用法：python webui_test.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT / "cateye_connect_all-in-one_client"
CONFIG_FILE = CLIENT_DIR / "config.json"
PLUGIN_CONFIG_FILE = CLIENT_DIR / "plugins/cateye_deepseek_harness/config.json"

WEBUI_PORT = 18767
PASSWORD = "test-pass-12345"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def write_test_config() -> None:
    CONFIG_FILE.write_text(json.dumps({
        "version": 1,
        "ws_url": "ws://127.0.0.1:19999",  # 无服务，框架会一直重连，不影响测试
        "token": "test",
        "reconnect_interval": 5,
        "webui": {"enabled": True, "host": "127.0.0.1", "port": WEBUI_PORT, "password": PASSWORD},
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup() -> None:
    for f in (CONFIG_FILE, CONFIG_FILE.with_suffix(".json.bak"), PLUGIN_CONFIG_FILE,
              PLUGIN_CONFIG_FILE.with_suffix(".json.bak")):
        if f.is_file():
            f.unlink()


async def main() -> None:
    cleanup()
    write_test_config()
    proc = subprocess.Popen(
        [sys.executable, "main.py"], cwd=str(CLIENT_DIR),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{WEBUI_PORT}"
    try:
        # 等待 WebUI 就绪
        # unsafe=True：aiohttp 默认不保存 IP 主机的 Cookie（RFC 行为），测试目标恰是 127.0.0.1
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    async with s.get(f"{base}/api/status", timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status in (200, 401):
                            break
                except Exception:
                    await asyncio.sleep(0.3)
            else:
                check("WebUI 启动", False, "15 秒内未就绪")
                return
            check("WebUI 启动", True)

            # 1. 鉴权
            async with s.get(f"{base}/api/status") as r:
                check("未登录访问被拒 (401)", r.status == 401)
            async with s.post(f"{base}/api/auth/verify", json={"password": "wrong"}) as r:
                check("错误密码被拒 (401)", r.status == 401)
            async with s.post(f"{base}/api/auth/verify", json={"password": PASSWORD}) as r:
                check("正确密码登录", r.status == 200)

            # 2. 状态
            async with s.get(f"{base}/api/status") as r:
                data = await r.json()
                names = [p["name"] for p in data.get("plugins", [])]
                check("status 含插件列表", "cateye_deepseek_harness" in names, str(names))
                check("status 含连接状态", isinstance(data.get("connected"), bool))

            # 3. 框架配置
            async with s.get(f"{base}/api/config/framework") as r:
                data = await r.json()
                keys = [f["key"] for g in data.get("schema", []) for f in g.get("fields", [])]
                check("框架 schema 含关键字段",
                      {"ws_url", "token", "webui.password", "ws_tls.insecure"} <= set(keys), str(keys))
                check("框架配置可读取", data.get("config", {}).get("token") == "test")
            async with s.post(f"{base}/api/config/framework",
                              json={"config": {"reconnect_interval": 9}}) as r:
                data = await r.json()
                check("框架配置保存成功", data.get("success") is True, str(data))
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            check("框架配置已写盘", saved.get("reconnect_interval") == 9)
            check("未知字段保留", saved.get("token") == "test" and saved.get("webui", {}).get("password") == PASSWORD)
            check("备份文件生成", CONFIG_FILE.with_suffix(".json.bak").is_file())

            # 4. 插件配置
            async with s.get(f"{base}/api/config/plugin/cateye_deepseek_harness") as r:
                data = await r.json()
                check("插件 schema 来自 plugin.json", bool(data.get("schema_declared")))
                check("插件配置可读取", data.get("config", {}).get("dsh", {}).get("cwd") is not None)
            async with s.post(f"{base}/api/config/plugin/cateye_deepseek_harness",
                              json={"config": {"features": {"screenshot": False},
                                               "screenshot": {"blur_radius": "12"}}}) as r:
                data = await r.json()
                check("插件配置保存 + 热更新", data.get("success") is True and data.get("hot_applied") is True, str(data))
            saved = json.loads(PLUGIN_CONFIG_FILE.read_text(encoding="utf-8"))
            check("插件配置已写盘", saved.get("features", {}).get("screenshot") is False)
            check("字符串数字已矫正", saved.get("screenshot", {}).get("blur_radius") == 12,
                  repr(saved.get("screenshot", {})))
            async with s.get(f"{base}/api/config/plugin/__no_such__") as r:
                check("未知插件返回 404", r.status == 404)

            # 5. 重连
            async with s.post(f"{base}/api/action/reconnect") as r:
                check("重连操作", (await r.json()).get("success") is True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        cleanup()

    print()
    if FAILURES:
        print(f"WebUI 测试失败: {FAILURES}")
        sys.exit(1)
    print("WebUI 测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
