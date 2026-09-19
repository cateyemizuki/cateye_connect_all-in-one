#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cateye Connect 本地端框架入口。

连接统一连接插件（MaiBot 端 cateye.connect-hub），把远端命令分发给
plugins/ 下的本地插件处理。业务逻辑全部在插件里，本文件只做组装。

用法：
  python main.py [--ws ws://127.0.0.1:8765] [--token <token>]

WebUI（配置编辑界面）默认随框架启动，地址见 config.json 的 webui 节
（默认 http://127.0.0.1:8766，首次启动自动生成访问密码并打印到控制台）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

CLIENT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(CLIENT_ROOT))

from framework import FRAMEWORK_VERSION
from framework.config import ensure_webui_password, load_config
from framework.log import Logger
from framework.plugin_loader import load_plugins
from framework.router import CommandRouter
from framework.ws_client import FrameworkClient


async def run(config: dict, log: Logger) -> None:
    client = FrameworkClient(config, log)
    plugins_dir = (CLIENT_ROOT / str(config.get("plugins_dir", "./plugins"))).resolve()
    plugins = load_plugins(plugins_dir, log, client.emit_event)
    if not plugins:
        log.warn("未加载任何本地插件，仅保持连接（所有命令都会返回未知命令）")
    router = CommandRouter(plugins, log)
    client.set_router(router)

    # WebUI：与框架同事件循环运行；依赖缺失/启动失败不影响框架本体
    webui = None
    if (config.get("webui") or {}).get("enabled", True):
        try:
            from framework.webui.server import WebUIServer
            webui = WebUIServer(config, client, router, log)
            await webui.start()
        except ImportError:
            log.warn("未安装 aiohttp，WebUI 已禁用（安装: pip install aiohttp）")
        except Exception as e:
            log.error("WebUI 启动失败: %s", e)

    try:
        await client.run_forever()
    finally:
        if webui is not None:
            await webui.stop()


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Cateye Connect 本地端框架")
    parser.add_argument("--ws", default=config.get("ws_url", ""), help="覆盖 config.json 的 ws_url")
    parser.add_argument("--token", default=config.get("token", ""), help="覆盖 config.json 的 token")
    parser.add_argument(
        "--autostart", choices=("on", "off", "status"), default=None,
        help="管理开机自启动（框架根目录的 开机自启-开启/关闭.cmd 即调用此参数）",
    )
    args = parser.parse_args()

    if args.autostart:
        from framework import autostart

        try:
            if args.autostart == "on":
                target = autostart.install()
                print(f"[OK] 已开启开机自启动: {target}")
            elif args.autostart == "off":
                removed = autostart.uninstall()
                print("[OK] 已关闭开机自启动" if removed else "[i] 开机自启动本就未开启")
            else:
                state = "已开启" if autostart.is_enabled() else "未开启"
                print(f"[i] 开机自启动: {state}（{autostart.shortcut_path()}）")
        except Exception as e:
            print(f"[X] 操作失败: {e}")
            sys.exit(1)
        return

    if args.ws:
        config["ws_url"] = args.ws
    if args.token:
        config["token"] = args.token

    if not config.get("token"):
        print("[!] 请提供 --token 或在 config.json 中配置 token")
        sys.exit(1)

    ensure_webui_password(config)

    log = Logger()
    log.info("Cateye Connect 本地端框架 v%s", FRAMEWORK_VERSION)
    log.info("服务端: %s", config["ws_url"])
    log.info("插件目录: %s", config.get("plugins_dir", "./plugins"))
    print()

    try:
        asyncio.run(run(config, log))
    except KeyboardInterrupt:
        print("\n[i] 已退出")


if __name__ == "__main__":
    main()
