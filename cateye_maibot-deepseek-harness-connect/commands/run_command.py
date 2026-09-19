"""/run 命令处理 — 经统一连接插件通信。"""

from typing import Any


class RunCommand:
    """/run <程序> — 在本地 PC 调用 Windows 运行窗口执行程序。"""

    @staticmethod
    async def handle(plugin, stream_id: str, **kwargs: Any) -> tuple[bool, str, bool]:
        user_id = plugin._resolve_user_id(kwargs, stream_id)
        if not plugin._is_admin(user_id):
            await plugin.ctx.send.text("权限不足，仅管理员可用。", stream_id)
            return False, "权限不足", True

        matched = kwargs.get("matched_groups", {})
        program = str(matched.get("program") or "").strip()
        if not program:
            await plugin.ctx.send.text("用法：/run <程序名>，如 /run registry", stream_id)
            return False, "缺少参数", True

        response = await plugin.hub.call(
            command="run",
            args={"program": program},
            user_id=user_id,
            timeout=plugin.config.harness.command_timeout,
        )
        if response.get("error"):
            await plugin.ctx.send.text(response["error"], stream_id)
            return False, response["error"], True
        await plugin.ctx.send.text(response.get("result", "已运行。"), stream_id)
        return True, "已运行", True
