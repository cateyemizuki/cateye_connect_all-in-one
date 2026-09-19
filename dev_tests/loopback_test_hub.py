#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端回环测试：真实 hub 服务端（ws_server.ConnectionHub）↔ 真实本地框架。

ws_server.py 只依赖 websockets 与 protocol.py，不需要 maibot_sdk，
因此可以在没有 MaiBot 的机器上验证真实服务端代码路径：
  1. 本地框架连接 + 鉴权 + 插件注册表上报
  2. call 模式：send_and_wait 同步等待响应（preset 列表）
  3. 未知命令：hub 直接报错（不发往本地）
  4. submit 模式：异步提交 → on_response 回调收到 (call_ref, result, error)

用法：python loopback_test_hub.py
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HUB_PKG = "cateye_connect_all-in-one_on_maibot"
CLIENT_DIR = ROOT / "cateye_connect_all-in-one_client"
PORT = 18766
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


async def main() -> None:
    protocol = importlib.import_module(f"{HUB_PKG}.protocol")
    ws_server = importlib.import_module(f"{HUB_PKG}.ws_server")
    ConnectionHub = ws_server.ConnectionHub

    callback_results: list[dict] = []

    async def on_response(call_id, response, reply_api, call_ref):
        callback_results.append(
            {"call_id": call_id, "reply_api": reply_api, "call_ref": call_ref,
             "result": response.get("result", ""), "error": response.get("error", "")}
        )

    hub = ConnectionHub(logger=TestLogger(), auth_timeout=10, on_response=on_response)
    await hub.start("127.0.0.1", PORT, TOKEN, 10 * 1024 * 1024)
    print(f"[i] 真实 hub 监听 127.0.0.1:{PORT}，启动本地框架...")

    proc = subprocess.Popen(
        [sys.executable, "main.py", "--ws", f"ws://127.0.0.1:{PORT}", "--token", TOKEN],
        cwd=str(CLIENT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        # 等待连接建立
        for _ in range(50):
            if hub.connected:
                break
            await asyncio.sleep(0.2)
        check("本地框架连接 + 鉴权 + 注册表上报", hub.connected)
        check("注册表包含 cateye_deepseek_harness",
              any(p.get("name") == "cateye_deepseek_harness" for p in hub.registry),
              str(hub.registry))
        check("known_commands 含 6 个命令",
              {"deepseek", "screenshot", "cmd_start", "cmd_stop", "run", "preset"} <= hub.known_commands,
              str(sorted(hub.known_commands)))

        # call 模式：preset 列表（无副作用）
        payload = protocol.make_request("call-1", "preset", {"name": "__no_such_preset__"}, "42")
        resp = await hub.send_and_wait(payload, timeout=15)
        check("call: preset 列表响应", "可用预设" in resp.get("error", ""), str(resp))
        payload = protocol.make_request("call-2", "preset", {"name": "__no_such_preset__"}, "42")
        resp = await hub.send_and_wait(payload, timeout=15)
        check("call: user_id 透传不影响响应", isinstance(resp.get("error"), str))

        # 未知命令：hub 侧直接拒绝
        payload = protocol.make_request("call-3", "__not_exist__", {}, "42")
        resp = await hub.send_and_wait(payload, timeout=15)
        check("call: 未知命令 hub 直接报错",
              "本地未加载可处理" in resp.get("error", ""), str(resp))

        # submit 模式：异步提交 → 回调
        payload = protocol.make_request("submit-1", "preset", {"name": "__no_such_preset__"}, "42")
        await hub.submit(payload, reply_api="test.plugin.on_result", call_ref="stream-abc")
        for _ in range(50):
            if callback_results:
                break
            await asyncio.sleep(0.2)
        cb = callback_results[0] if callback_results else {}
        check("submit: 回调被触发且带 call_ref/result/error",
              cb.get("call_ref") == "stream-abc" and "可用预设" in cb.get("error", "") and
              cb.get("reply_api") == "test.plugin.on_result", str(cb))

        # 踢线竞态回归：新连接顶掉旧连接后，hub.connected 必须仍为新连接（不得被旧连接收尾误清）
        import websockets

        async def fake_client(name: str) -> "websockets.WebSocketClientProtocol":
            ws = await websockets.connect(f"ws://127.0.0.1:{PORT}")
            await ws.send(protocol.encode({
                "type": protocol.AUTH, "token": TOKEN, "version": 2,
                "client": name, "plugins": [{"name": "fake", "commands": ["fake_cmd"]}],
            }))
            resp = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=5))
            assert resp.get("type") == protocol.AUTH_OK, resp
            return ws

        old_ws = await fake_client("fake-old")
        new_ws = await fake_client("fake-new")  # 顶掉 old
        await asyncio.sleep(0.5)  # 等 old 连接的收尾清理跑完
        check("踢线竞态: 新连接未被旧连接收尾误清",
              hub.connected and hub.client_name == "fake-new", f"client={hub.client_name!r}")
        payload = protocol.make_request("call-kick", "fake_cmd", {}, "")
        resp = await hub.send_and_wait(payload, timeout=1.5)
        check("踢线竞态: 请求可达新连接（超时报错而非未连接）",
              resp.get("error") == protocol.ERR_TIMEOUT, str(resp))

        # 断连时 submit 回调以断连错误触发（不静默丢弃）
        cb_marker = len(callback_results)
        payload = protocol.make_request("submit-kick", "fake_cmd", {}, "")
        await hub.submit(payload, reply_api="test.plugin.on_kick", call_ref="stream-x")
        newer_ws = await fake_client("fake-newer")  # 顶掉 fake-new，触发其收尾清理
        for _ in range(50):
            if len(callback_results) > cb_marker:
                break
            await asyncio.sleep(0.2)
        kicked_cb = callback_results[cb_marker] if len(callback_results) > cb_marker else {}
        check("submit: 断连时回调以断连错误触发（不静默丢弃）",
              kicked_cb.get("call_ref") == "stream-x"
              and kicked_cb.get("error") == protocol.ERR_DISCONNECTED
              and kicked_cb.get("reply_api") == "test.plugin.on_kick", str(kicked_cb))
        await old_ws.close()
        await new_ws.close()
        await newer_ws.close()
        # 断开 fake 连接后框架仍在（等重连），不影响后续输出
        await asyncio.sleep(0.5)

        print()
        if FAILURES:
            print(f"回环测试失败: {FAILURES}")
        else:
            print("回环测试全部通过")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        await hub.stop()


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print(f"[i] 耗时 {time.time() - t0:.1f}s")
    sys.exit(1 if FAILURES else 0)
