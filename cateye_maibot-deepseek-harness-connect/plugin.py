"""DeepSeek Harness 连接插件 — MaiBot 端业务插件。

自己不建立任何连接：与本地 PC 的所有通信都经统一连接插件
（cateye.connect-hub）的公共 API 完成：
  - 普通命令（CMD/run/preset）：hub.call，command_timeout 超时
  - /dsh 长任务：hub.submit 异步提交，本地完成后经本插件动态 API
    cateye.deepseek-harness-connect.on_result 回调发送结果（无超时语义）
  - 连接预检：hub.status

截图（/sj）由统一连接插件直接提供，不在本插件。

权限校验（admin）、长文本拆分等业务逻辑保留在本插件。
"""

from __future__ import annotations

from typing import Any

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase

from .commands.cmd_command import CmdCommand
from .commands.deepseek_command import DeepseekCommand
from .commands.preset_command import PresetCommand
from .commands.run_command import RunCommand
from .hub_client import HubClient, ON_RESULT_API

SUPPORTED_CONFIG_VERSION = "1.0.0"
PLUGIN_ID = "cateye.deepseek-harness-connect"


# ============================================================================
# 配置模型
# ============================================================================

class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra={
            "label": {"zh_CN": "启用插件", "en_US": "Enable Plugin"},
            "x-widget": "switch",
        },
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置版本（与插件版本同步）",
        json_schema_extra={"hidden": True, "disabled": True},
    )


class HarnessConfig(PluginConfigBase):
    __ui_label__ = "DeepSeek Harness"
    __ui_icon__ = "terminal"
    __ui_order__ = 1

    admin_users: list[str] = Field(
        default_factory=list,
        description="管理员 QQ 号列表（/dsh、/cmd、/sdcmd、/run、/preset 均要求管理员）",
        json_schema_extra={"label": {"zh_CN": "管理员 QQ 号", "en_US": "Admin QQ IDs"}},
    )
    command_timeout: int = Field(
        default=60,
        description="等待本地响应秒数（CMD/run/preset）",
        json_schema_extra={"label": {"zh_CN": "命令等待（秒）", "en_US": "Command Timeout (s)"}},
    )


class DeepseekHarnessConnectConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)


# ============================================================================
# 长内容拆分转发
# ============================================================================

MAX_MSG_LEN = 500


def _split_long_text(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    paras = text.split("\n\n")
    for para in paras:
        if len(para) <= max_len:
            chunks.append(para)
        else:
            lines = para.split("\n")
            buf = ""
            for line in lines:
                if len(buf) + len(line) + 1 <= max_len:
                    buf = (buf + "\n" + line) if buf else line
                else:
                    if buf:
                        chunks.append(buf)
                    buf = line
            if buf:
                chunks.append(buf)
    merged: list[str] = []
    for c in chunks:
        if merged and len(merged[-1]) + len(c) + 2 <= max_len:
            merged[-1] = merged[-1] + "\n\n" + c
        else:
            merged.append(c)
    return merged


# ============================================================================
# 插件主体
# ============================================================================

class DeepseekHarnessConnectPlugin(MaiBotPlugin):
    """DeepSeek Harness 连接插件（经统一连接插件通信）。"""

    config_model = DeepseekHarnessConnectConfig

    async def on_load(self) -> None:
        self.ctx.logger.info("DeepSeek Harness 连接插件已加载（经统一连接插件通信）")
        self.hub = HubClient(self)
        # /dsh 长任务的结果回调（hub.submit 的响应打到这）
        self.register_dynamic_api(
            ON_RESULT_API,
            self._on_dsh_result,
            description="DeepSeek Harness 长任务结果回调（call_ref=, result=, error=）",
            version="1",
            public=True,
        )
        await self.sync_dynamic_apis()

    async def on_unload(self) -> None:
        self.ctx.logger.info("DeepSeek Harness 连接插件正在卸载")
        self.clear_dynamic_apis()
        await self.sync_dynamic_apis(offline_reason="插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del scope, config_data, version

    # ===== 权限与辅助 =====

    @property
    def reply_api(self) -> str:
        """/dsh 提交时告知 hub 的回调 API 全名。"""
        return f"{PLUGIN_ID}.{ON_RESULT_API}"

    def _is_admin(self, user_id: str) -> bool:
        admins = self.config.harness.admin_users
        return str(user_id) in [str(a) for a in admins]

    def _resolve_user_id(self, kwargs: dict, fallback: str = "") -> str:
        uid = kwargs.get("user_id") or kwargs.get("sender_id") or ""
        if not uid:
            msg = kwargs.get("message", {})
            if isinstance(msg, dict):
                uid = str(msg.get("user_id") or msg.get("sender", {}).get("user_id", ""))
        return uid or fallback

    async def _send_long_text(self, text: str, stream_id: str) -> None:
        chunks = _split_long_text(text)
        if len(chunks) == 1:
            await self.ctx.send.text(chunks[0], stream_id)
            return
        try:
            messages = [
                {"user_id": "0", "nickname": "DeepSeek", "segments": [{"type": "text", "content": c}]}
                for c in chunks
            ]
            await self.ctx.send.forward(messages, stream_id)
        except Exception:
            for i, c in enumerate(chunks, 1):
                await self.ctx.send.text(f"({i}/{len(chunks)})\n{c}", stream_id)

    # ===== /dsh 结果回调 =====

    async def _on_dsh_result(
        self, call_ref: str = "", result: str = "", error: str = "", **kwargs: Any
    ) -> dict:
        """hub.submit 的回调：把 /dsh 结果发送到原聊天流（call_ref = stream_id）。"""
        del kwargs
        stream_id = call_ref
        if not stream_id:
            self.ctx.logger.warning("dsh 回调缺少 call_ref，无法回发结果")
            return {"success": False}
        try:
            if error:
                await self.ctx.send.text(error, stream_id)
            elif result:
                await self._send_long_text(result, stream_id)
            else:
                await self.ctx.send.text("(无返回内容)", stream_id)
        except Exception as e:
            self.ctx.logger.error("dsh 结果发送失败: %s", e)
        return {"success": True}

    # ========================================================================
    # Command: /deepseek（别名 /dsh，逻辑在 commands/deepseek_command.py）
    # ========================================================================

    @Command(
        "deepseek_chat",
        description="向本地 DeepSeek Harness 发送提示词",
        pattern=r"^/(?:deepseek|dsh)(?:\s+(?P<rest>[\s\S]+))?$",
    )
    async def cmd_deepseek(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        return await DeepseekCommand.handle(self, stream_id, **kwargs)

    # ========================================================================
    # Command: /cmd（别名 /c）与 /sdcmd（逻辑在 commands/cmd_command.py）
    # ========================================================================

    @Command(
        "cmd_run",
        description="在本地 CMD 窗口运行命令（支持多行）",
        pattern=r"^/(?:cmd|c)\s+(?P<window_id>\d+)\s+(?P<command>[\s\S]+)$",
    )
    async def cmd_run(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        return await CmdCommand.handle_start(self, stream_id, **kwargs)

    @Command(
        "cmd_stop",
        description="关闭 CMD 窗口：/sdcmd 或 /sc <窗口序号>；/sdcmd 或 /sc /p|/pid <PID>",
        pattern=r"^/(?:sdcmd|sc)\s+(?:(?P<mode>/p|/pid)\s+)?(?P<target>\d+)$",
    )
    async def cmd_stop(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        return await CmdCommand.handle_stop(self, stream_id, **kwargs)

    # ========================================================================
    # Command: /run（逻辑在 commands/run_command.py）
    # ========================================================================

    @Command(
        "run_program",
        description="调用 Windows 运行窗口运行程序",
        pattern=r"^/run\s+(?P<program>\S+)$",
    )
    async def cmd_run_program(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        return await RunCommand.handle(self, stream_id, **kwargs)

    # ========================================================================
    # Command: /preset（别名 /p，逻辑在 commands/preset_command.py）
    # ========================================================================

    @Command(
        "run_preset",
        description="运行预设脚本",
        pattern=r"^/(?:preset|p)\s+(?P<name>\S+)$",
    )
    async def cmd_preset(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        return await PresetCommand.handle(self, stream_id, **kwargs)


def create_plugin() -> DeepseekHarnessConnectPlugin:
    return DeepseekHarnessConnectPlugin()
