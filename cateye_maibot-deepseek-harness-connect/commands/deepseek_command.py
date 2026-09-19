"""/deepseek 命令处理（别名 /dsh）— 经统一连接插件通信。"""

from typing import Any


class DeepseekCommand:
    """/deepseek <工作区名称> <对话内容>（可缩写 /dsh）— 发送提示词给本地 DeepSeek Harness。

    工作区名称用于在本地插件 data/deepseek/ 下区分对话目录；
    Harness 输出通过合并消息返回 QQ，不返回思维链，只返回输出结果。

    处理方式：hub.submit 异步提交后命令立即回复「已收到」；
    Harness 属于大模型长耗时工作，不设超时——本地完成后经本插件
    动态 API（cateye.deepseek-harness-connect.on_result）回调发送结果，
    连接断开时由统一连接插件统一唤醒报错。
    """

    @staticmethod
    async def handle(plugin, stream_id: str, **kwargs: Any) -> tuple[bool, str, bool]:
        user_id = plugin._resolve_user_id(kwargs, stream_id)
        if not plugin._is_admin(user_id):
            await plugin.ctx.send.text("权限不足，仅管理员可使用 DeepSeek Harness 远程对话。", stream_id)
            return False, "权限不足", True

        matched = kwargs.get("matched_groups", {})
        raw_rest = (matched.get("rest") or "").strip()

        # 解析 [工作区名称] [对话内容]：首个空白分隔的 token 为工作区名称，其余为对话内容
        parts = raw_rest.split(None, 1)
        workspace = parts[0].strip() if parts else ""
        content = parts[1].strip() if len(parts) > 1 else ""

        if not workspace or not content:
            await plugin.ctx.send.text(
                "用法：/deepseek <工作区名称> <对话内容>（可缩写 /dsh），如：/deepseek test 你是什么模型",
                stream_id,
            )
            return False, "缺少参数", True

        # 长任务：异步提交（未连接/未知命令时 submit 立即返回错误）
        resp = await plugin.hub.submit(
            command="deepseek",
            args={"prompt": content, "workspace": workspace},
            user_id=user_id,
            reply_api=plugin.reply_api,
            call_ref=stream_id,
        )
        if not resp.get("success"):
            err = resp.get("error", "提交失败")
            await plugin.ctx.send.text(err, stream_id)
            return False, err, True

        await plugin.ctx.send.text("已收到，正在调用 DeepSeek Harness 处理，完成后将回复结果。", stream_id)
        return True, "已开始处理", True
