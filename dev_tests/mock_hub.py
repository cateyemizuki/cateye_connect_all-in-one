#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mock_hub — 不启动 MaiBot 联调本地框架与插件。

模拟统一连接插件（cateye.connect-hub）的最小行为：
  1. 监听端口，等待本地框架连接并完成鉴权（打印上报的插件注册表）
  2. 发送一个未知命令请求 → 期望返回「未知命令」错误
  3. 发送 preset 列表请求（无副作用）→ 期望返回可用预设列表
  4. （可选）--screenshot：真实截图请求

用法：
  python mock_hub.py [--host 127.0.0.1] [--port 18765] [--token test] [--screenshot]

另开终端运行本地框架：
  cd cateye_connect_all-in-one_client
  python main.py --ws ws://127.0.0.1:18765 --token test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import websockets


async def recv_json(ws, timeout: float = 15) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(raw)
    print(f"    <- {json.dumps(data, ensure_ascii=False)[:300]}")
    return data


async def send_request(ws, command: str, args: dict) -> dict:
    frame = {
        "type": "request",
        "call_id": str(uuid.uuid4()),
        "command": command,
        "args": args,
        "user_id": "mock",
    }
    print(f"    -> {command} {json.dumps(args, ensure_ascii=False)[:200]}")
    await ws.send(json.dumps(frame, ensure_ascii=False))
    return await recv_json(ws)


async def run(args: argparse.Namespace) -> None:
    async def handler(ws):
        print("[hub] 等待本地框架连接...")
        auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        if auth.get("type") != "auth" or auth.get("token") != args.token:
            await ws.send(json.dumps({"type": "auth_error", "error": "鉴权失败：token 不正确"}))
            await ws.close()
            return
        await ws.send(json.dumps({"type": "auth_ok", "version": 2, "server": "mock-hub"}))
        print("[hub] 鉴权通过")
        print(f"    client: {auth.get('client')}")
        for p in auth.get("plugins", []):
            print(f"    插件: {p.get('name')} v{p.get('version')} 命令: {', '.join(p.get('commands', []))}")

        failures = []

        # 1. 未知命令
        resp = await send_request(ws, "__not_exist__", {})
        ok = resp.get("error") and "未知命令" in resp.get("error", "")
        print(f"[test] 未知命令错误: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append("未知命令")

        # 2. preset 列表（无副作用）
        resp = await send_request(ws, "preset", {"name": "__no_such_preset__"})
        ok = resp.get("error") and ("可用预设" in resp.get("error", "") or "未启用" in resp.get("error", ""))
        print(f"[test] preset 列表: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append("preset 列表")

        # 3. 可选：真实截图
        if args.screenshot:
            resp = await send_request(ws, "screenshot", {"blur_radius": 0})
            ok = bool(resp.get("result")) and resp["result"].startswith("iVBOR")
            print(f"[test] 截图: {'PASS' if ok else 'FAIL'}")
            if not ok:
                failures.append("screenshot")

        print("[hub] 全部通过" if not failures else f"[hub] 失败项: {failures}")
        await ws.close()

    async with websockets.serve(handler, args.host, args.port):
        print(f"[hub] mock hub 监听 ws://{args.host}:{args.port}（token={args.token}）")
        await asyncio.Future()  # 运行至 Ctrl+C


def main() -> None:
    parser = argparse.ArgumentParser(description="cateye connect mock hub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--token", default="test")
    parser.add_argument("--screenshot", action="store_true", help="追加真实截图测试")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
