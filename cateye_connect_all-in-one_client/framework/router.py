"""命令路由：command → 本地插件。"""

from __future__ import annotations

from .log import Logger
from .plugin_base import LocalPlugin


class CommandRouter:
    def __init__(self, plugins: list[LocalPlugin], logger: Logger):
        self._logger = logger
        self._plugins = list(plugins)
        self._routes: dict[str, LocalPlugin] = {}
        for p in self._plugins:
            for c in p.commands:
                self._routes[c] = p

    @property
    def plugins(self) -> list[LocalPlugin]:
        """已加载插件（WebUI 等外部组件按此枚举）。"""
        return list(self._plugins)

    @property
    def registry(self) -> list[dict]:
        """握手时上报给 hub 的插件注册表。"""
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "commands": list(p.commands),
            }
            for p in self._plugins
        ]

    async def dispatch(self, call_id: str, command: str, args: dict, user_id: str) -> dict:
        """分发命令，返回统一响应形态 {"call_id", "result", "error"}。"""
        plugin = self._routes.get(command)
        if plugin is None:
            return {"call_id": call_id, "result": "", "error": f"未知命令: {command}"}
        try:
            resp = await plugin.handle_command(call_id, command, args or {}, user_id or "")
        except Exception as e:
            self._logger.error("命令 %s 处理异常: %s", command, e)
            return {"call_id": call_id, "result": "", "error": str(e)}
        if not isinstance(resp, dict):
            resp = {"result": "" if resp is None else str(resp), "error": ""}
        return {
            "call_id": call_id,
            "result": resp.get("result", ""),
            "error": resp.get("error", ""),
        }
