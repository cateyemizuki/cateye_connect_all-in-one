#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多屏截图端到端回环测试：真实 hub 服务端 ↔ 真实本地框架 ↔ 真实截图处理。

在 loopback_test_hub.py 基础上只验证 screenshot 链路（不写 config.json，
连接参数经 --ws/--token 覆盖）：
  1. response.result 恒为主屏截图（primary=True 的那一项）
  2. data.screens 每屏一项、主屏在第 0 位，元数据齐全（left/top/width/height）
  3. blur 参数路径可用

用法（用客户端 venv 运行，需 websockets + PIL）：
  python dev_tests/loopback_test_screenshot.py
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HUB_PKG = "cateye_connect_all-in-one_on_maibot"
CLIENT_DIR = ROOT / "cateye_connect_all-in-one_client"
PORT = 18768
TOKEN = "test"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


class TestLogger:
    def info(self, msg, *args):
        print("    [hub]", msg % args if args else msg)

    def warning(self, msg, *args):
        print("    [hub!]", msg % args if args else msg)

    def error(self, msg, *args):
        print("    [hubX]", msg % args if args else msg)


def png_size(image_b64: str):
    raw = base64.b64decode(image_b64[:64], validate=False)
    if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", raw[16:24])
    return None, None


async def main() -> int:
    protocol = importlib.import_module(f"{HUB_PKG}.protocol")
    ws_server = importlib.import_module(f"{HUB_PKG}.ws_server")
    hub = ws_server.ConnectionHub(logger=TestLogger(), auth_timeout=10)
    # 与新默认一致：25MB 帧上限（auth_ok 会把该值通告给本地端）
    await hub.start("127.0.0.1", PORT, TOKEN, 25 * 1024 * 1024)
    print(f"[i] hub 监听 127.0.0.1:{PORT}，启动本地框架...")

    proc = subprocess.Popen(
        [sys.executable, "main.py", "--ws", f"ws://127.0.0.1:{PORT}", "--token", TOKEN],
        cwd=str(CLIENT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(50):
            if hub.connected:
                break
            await asyncio.sleep(0.2)
        check("本地框架连接 + 鉴权", hub.connected)

        # ── 截图：result=主屏 + data.screens 明细 ──
        payload = protocol.make_request("shot-1", "screenshot", {"blur_radius": 0}, "42")
        resp = await hub.send_and_wait(payload, timeout=60)
        result_b64 = resp.get("result", "")
        check("截图响应无错误", not resp.get("error"), str(resp.get("error", ""))[:120])
        check("result 非空（base64 PNG）", result_b64.startswith("iVBORw0"), f"{len(result_b64)} chars")

        data = resp.get("data") or {}
        screens = data.get("screens") or []
        check("data.screens 存在且 ≥1 项", len(screens) >= 1, f"screen_count={data.get('screen_count')}")
        check("主屏在第 0 位（primary=True）",
              bool(screens) and screens[0].get("primary") is True,
              str([(s.get("primary"), s.get("width"), s.get("height")) for s in screens]))
        check("result == 主屏 image_base64",
              bool(screens) and screens[0].get("image_base64") == result_b64)
        check("每屏元数据齐全",
              all(isinstance(s.get(k), int) for s in screens for k in ("index", "left", "top", "width", "height")),
              str([{k: s.get(k) for k in ("left", "top", "width", "height")} for s in screens]))
        w, h = png_size(result_b64)
        check("主屏 PNG 尺寸 == 元数据",
              bool(screens) and (w, h) == (screens[0].get("width"), screens[0].get("height")),
              f"png={w}x{h}")
        if len(screens) > 1:
            check("副屏图像非空", bool(screens[1].get("image_base64")),
                  f"{len(screens[1].get('image_base64', ''))} chars")

        # ── 模糊路径 ──
        payload = protocol.make_request("shot-2", "screenshot", {"blur_radius": 8}, "42")
        resp2 = await hub.send_and_wait(payload, timeout=60)
        check("blur=8 响应正常", not resp2.get("error") and resp2.get("result", "").startswith("iVBORw0"),
              f"{len(resp2.get('result', ''))} chars")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        await hub.stop()

    print()
    if FAILURES:
        print(f"多屏截图回环测试失败: {FAILURES}")
        return 1
    print("多屏截图回环测试全部通过")
    return 0


if __name__ == "__main__":
    t0 = time.time()
    code = asyncio.run(main())
    print(f"[i] 耗时 {time.time() - t0:.1f}s")
    sys.exit(code)
