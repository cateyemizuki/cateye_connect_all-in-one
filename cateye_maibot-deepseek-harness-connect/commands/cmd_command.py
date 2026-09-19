"""/cmd 与 /sdcmd 命令处理 — 经统一连接插件通信。"""

from typing import Any


class CmdCommand:
    """/cmd <窗口序号> "<命令>" — 在本地客户端 CMD 窗口运行命令
       /sdcmd|/sc <窗口序号> — 关闭指定 CMD 窗口
       /sdcmd|/sc /p|/pid <PID> — 结束指定 PID 的进程树（/p 为 /pid 简写）"""

    @staticmethod
    async def handle_start(plugin, stream_id: str, **kwargs: Any) -> tuple[bool, str, bool]:
        """/cmd <id> <命令>"""
        user_id = plugin._resolve_user_id(kwargs, stream_id)
        if not plugin._is_admin(user_id):
            await plugin.ctx.send.text("权限不足，仅管理员可用 CMD 命令。", stream_id)
            return False, "权限不足", True

        matched = kwargs.get("matched_groups", {})
        win_text = str(matched.get("window_id") or "").strip()
        command = str(matched.get("command") or "").strip()

        if not command:
            await plugin.ctx.send.text("用法：/cmd <窗口序号> \"<执行命令>\"", stream_id)
            return False, "缺少参数", True

        try:
            win_id = int(win_text)
        except ValueError:
            await plugin.ctx.send.text("无效的窗口序号。用法：/cmd <窗口序号> \"<命令>\"", stream_id)
            return False, "窗口序号无效", True

        response = await plugin.hub.call(
            command="cmd_start",
            args={"window_id": win_id, "command": command},
            user_id=user_id,
            timeout=plugin.config.harness.command_timeout,
        )
        if response.get("error"):
            await plugin.ctx.send.text(response["error"], stream_id)
            return False, response["error"], True
        await plugin.ctx.send.text(response.get("result", "命令已执行。"), stream_id)
        return True, "CMD 已执行", True

    @staticmethod
    async def handle_stop(plugin, stream_id: str, **kwargs: Any) -> tuple[bool, str, bool]:
        """/sdcmd|/sc <窗口序号> 或 /sdcmd|/sc /p|/pid <PID>"""
        user_id = plugin._resolve_user_id(kwargs, stream_id)
        if not plugin._is_admin(user_id):
            await plugin.ctx.send.text("权限不足，仅管理员可用。", stream_id)
            return False, "权限不足", True

        matched = kwargs.get("matched_groups", {})
        mode = str(matched.get("mode") or "").strip()
        target_text = str(matched.get("target") or "").strip()

        try:
            target = int(target_text)
        except ValueError:
            await plugin.ctx.send.text(
                "无效的序号。用法：/sdcmd|/sc <窗口序号> 或 /sdcmd|/sc /p|/pid <PID>", stream_id
            )
            return False, "序号无效", True

        # /p|/pid 分支：结束指定 PID 进程树；否则为窗口序号分支
        if mode in ("/p", "/pid"):
            args = {"pid": target}
        else:
            args = {"window_id": target}

        response = await plugin.hub.call(
            command="cmd_stop",
            args=args,
            user_id=user_id,
            timeout=plugin.config.harness.command_timeout,
        )
        if response.get("error"):
            await plugin.ctx.send.text(response["error"], stream_id)
            return False, response["error"], True
        await plugin.ctx.send.text(response.get("result", "窗口已关闭。"), stream_id)
        return True, "CMD 已关闭", True
