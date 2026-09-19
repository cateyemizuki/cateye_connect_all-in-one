"""/preset 命令处理 — 经统一连接插件通信。"""

from typing import Any


class PresetCommand:
    """/preset <名称> — 运行本地 data/preset 文件夹中的预设文件/脚本（忽略后缀）。"""

    @staticmethod
    async def handle(plugin, stream_id: str, **kwargs: Any) -> tuple[bool, str, bool]:
        user_id = plugin._resolve_user_id(kwargs, stream_id)
        if not plugin._is_admin(user_id):
            await plugin.ctx.send.text("权限不足，仅管理员可用。", stream_id)
            return False, "权限不足", True

        matched = kwargs.get("matched_groups", {})
        name = str(matched.get("name") or "").strip()
        if not name:
            await plugin.ctx.send.text("用法：/preset <预设名称>", stream_id)
            return False, "缺少参数", True

        response = await plugin.hub.call(
            command="preset",
            args={"name": name},
            user_id=user_id,
            timeout=plugin.config.harness.command_timeout,
        )
        if response.get("error"):
            await plugin.ctx.send.text(response["error"], stream_id)
            return False, response["error"], True
        await plugin.ctx.send.text(response.get("result", "预设已运行。"), stream_id)
        return True, "预设已运行", True
