"""本地插件基类与上下文。

开发新插件参见 cateye_connect_all-in-one/docs/dev-local-plugin.md：
  1. 在 plugins/<你的插件>/ 放置 plugin.json 与 plugin.py
  2. plugin.py 提供 create_plugin(ctx) 工厂，返回 LocalPlugin 子类实例
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable


class PluginContext:
    """框架注入给插件的运行环境。"""

    def __init__(
        self,
        name: str,
        plugin_dir: Path,
        data_dir: Path,
        logger,
        emit_event: Callable[[str, dict], Awaitable[bool]],
    ):
        self.name = name
        self.plugin_dir = plugin_dir
        self.data_dir = data_dir
        self.logger = logger
        self._emit_event = emit_event

    def load_plugin_config(self, defaults: dict | None = None) -> dict:
        """读取插件目录下 config.json（可选），缺失/失败时返回 defaults。"""
        cfg = dict(defaults or {})
        path = self.plugin_dir / "config.json"
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    cfg.update(raw)
            except Exception as e:
                self.logger.warn("config.json 加载失败，使用默认配置: %s", e)
        return cfg

    async def emit_event(self, event: str, data: dict | None = None) -> bool:
        """向 MaiBot 端推送事件帧（未连接时返回 False，不抛异常）。"""
        return await self._emit_event(event, data or {})


class LocalPlugin:
    """本地插件基类：声明 commands，实现 handle_command。

    框架负责 WS 连接、重连、鉴权与路由；插件只写业务。
    """

    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    commands: list[str] = []

    def __init__(self, ctx: PluginContext):
        self.ctx = ctx

    async def on_load(self) -> None:
        """插件加载后调用（可选实现）。"""

    async def on_unload(self) -> None:
        """插件卸载前调用（可选实现）。"""

    async def handle_command(self, call_id: str, command: str, args: dict, user_id: str) -> dict:
        """处理一条命令，返回 {"result": str, "error": str}。"""
        raise NotImplementedError

    async def on_config_update(self, config: dict) -> bool:
        """插件配置被 WebUI 修改保存后调用（可选实现），config 为合并后的完整配置。

        返回 True 表示已就地应用新配置（立即生效，无需重启框架）；
        默认实现返回 False（修改写入文件，框架重启后生效）。
        """
        del config
        return False
