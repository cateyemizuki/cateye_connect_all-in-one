"""Cateye 统一连接插件（hub）— MaiBot 端与本地 PC 之间的唯一 WebSocket 通道。

职责：
  - 持有 WS 服务端，管理本地框架的连接（鉴权、单客户端、断连清理）
  - 对其他 MaiBot 插件暴露公共 API（全名 cateye.connect-hub.<name>）：
      call        同步发送命令并等待响应
      submit      异步提交命令，响应经调用方动态 API 回调（长耗时任务）
      status      连接状态与本地插件注册表
      subscribe / unsubscribe  订阅本地端上行事件
      screenshot  获取本地 PC 截图（base64 PNG）
  - 截图命令 /sj（/screenshot）由本插件直接提供：模糊策略与限流集中在此，
    其他插件也可经 screenshot API 取图（自行嵌入消息、渲染等）
  - 分发本地主动上行的 event 帧给订阅者

业务插件（如 deepseek harness）不应自行建立连接，也不应重复实现截图，
一律通过本插件 API 通信。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

from maibot_sdk import API, Command, Field, MaiBotPlugin, PluginConfigBase

from . import protocol
from .ws_server import ConnectionHub

SUPPORTED_CONFIG_VERSION = "1.0.0"

# 截图模糊半径钳制上限：异常大半径会导致 PIL 模糊计算 CPU/内存失控
MAX_BLUR_RADIUS = 64


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


class ConnectionConfig(PluginConfigBase):
    __ui_label__ = "本地连接"
    __ui_icon__ = "cable"
    __ui_order__ = 1

    ws_host: str = Field(
        default="0.0.0.0",
        description="WebSocket 监听地址：0.0.0.0=所有网卡（默认，与旧版一致）；127.0.0.1=仅本机可连",
        json_schema_extra={
            "label": {"zh_CN": "监听地址", "en_US": "Listen Host"},
            "x-placeholder": "0.0.0.0",
        },
    )
    ws_port: int = Field(
        default=8765,
        description="WebSocket 服务端口",
        json_schema_extra={"label": {"zh_CN": "服务端口", "en_US": "Port"}},
    )
    ws_token: str = Field(
        default="",
        description="鉴权 Token（本地框架连接时必须携带）；留空则服务不启动",
        json_schema_extra={
            "label": {"zh_CN": "鉴权 Token", "en_US": "Auth Token"},
            "x-widget": "password",
        },
    )
    auth_timeout: int = Field(
        default=10,
        description="鉴权等待秒数",
        json_schema_extra={"label": {"zh_CN": "鉴权等待（秒）", "en_US": "Auth Timeout (s)"}},
    )
    max_frame_mb: int = Field(
        default=10,
        description="单帧大小上限（MB，截图 base64 用）",
        json_schema_extra={"label": {"zh_CN": "单帧上限（MB）", "en_US": "Max Frame (MB)"}},
    )


class ScreenshotConfig(PluginConfigBase):
    __ui_label__ = "截图"
    __ui_icon__ = "screenshot"
    __ui_order__ = 2

    admin_users: list[str] = Field(
        default_factory=list,
        description="管理员 QQ 号列表（管理员截图不模糊）",
        json_schema_extra={"label": {"zh_CN": "管理员 QQ 号", "en_US": "Admin QQ IDs"}},
    )
    blur_radius: int = Field(
        default=5,
        description="非管理员高斯模糊半径（0-64）",
        json_schema_extra={"label": {"zh_CN": "非管理员模糊半径", "en_US": "Blur Radius (non-admin)"}},
    )
    max_per_minute: int = Field(
        default=10,
        description="每分钟全局截图最大请求数",
        json_schema_extra={"label": {"zh_CN": "全局限流（次/分钟）", "en_US": "Global Rate (/min)"}},
    )
    max_per_user_per_minute: int = Field(
        default=2,
        description="每用户每分钟截图最大请求数",
        json_schema_extra={"label": {"zh_CN": "单用户限流（次/分钟）", "en_US": "Per-User Rate (/min)"}},
    )
    command_timeout: int = Field(
        default=60,
        description="等待本地截图响应秒数",
        json_schema_extra={"label": {"zh_CN": "截图等待（秒）", "en_US": "Screenshot Timeout (s)"}},
    )


class ConnectHubConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    connection: ConnectionConfig = Field(default_factory=ConnectionConfig)
    screenshot: ScreenshotConfig = Field(default_factory=ScreenshotConfig)


# ============================================================================
# 截图频率限制
# ============================================================================

class ScreenshotRateLimiter:
    def __init__(self, max_per_minute: int = 10, max_per_user_per_minute: int = 2):
        self.max_per_minute = max_per_minute
        self.max_per_user_per_minute = max_per_user_per_minute
        self._global_timestamps: list[float] = []
        self._user_timestamps: dict[str, list[float]] = {}

    def check(self, user_id: str) -> Optional[str]:
        now = time.time()
        window = now - 60
        self._global_timestamps = [t for t in self._global_timestamps if t > window]
        if len(self._global_timestamps) >= self.max_per_minute:
            return "截图操作过于频繁，请一分钟后再试"
        user_ts = self._user_timestamps.setdefault(user_id, [])
        user_ts[:] = [t for t in user_ts if t > window]
        if len(user_ts) >= self.max_per_user_per_minute:
            return "截图操作过于频繁，请一分钟后再试"
        self._global_timestamps.append(now)
        user_ts.append(now)
        return None


# ============================================================================
# 插件主体
# ============================================================================

class CateyeConnectHubPlugin(MaiBotPlugin):
    """统一连接插件。"""

    config_model = ConnectHubConfig

    async def on_load(self) -> None:
        self.ctx.logger.info("Cateye 统一连接插件已加载")
        self._bg_tasks: set[asyncio.Task] = set()
        self._rate_limiter = self._build_limiter()
        self._hub = self._build_hub()
        await self._start_server()

    async def on_unload(self) -> None:
        self.ctx.logger.info("Cateye 统一连接插件正在卸载")
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()
        await self._hub.stop()

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del scope, config_data, version
        old_hub = self._hub
        await old_hub.stop()
        self._rate_limiter = self._build_limiter()
        self._hub = self._build_hub()
        # 事件订阅由 MaiBot 侧插件登记，不能因配置热更新静默丢失
        self._hub.adopt_subscriptions(old_hub)
        await self._start_server()

    # ===== 构建与 WS 服务 =====

    def _build_hub(self) -> ConnectionHub:
        return ConnectionHub(
            logger=self.ctx.logger,
            auth_timeout=float(self.config.connection.auth_timeout),
            on_response=self._on_submit_response,
            on_event=self._on_event,
        )

    def _build_limiter(self) -> ScreenshotRateLimiter:
        return ScreenshotRateLimiter(
            self.config.screenshot.max_per_minute,
            self.config.screenshot.max_per_user_per_minute,
        )

    async def _start_server(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.warning("插件未启用，跳过 WebSocket 服务启动")
            return
        cfg = self.config.connection
        if not cfg.ws_token:
            # 不让 on_load 失败：插件保持已加载状态，配置补上 token 后保存即自动启动
            self.ctx.logger.warning(
                "WebSocket 服务未启动：ws_token 未设置。请在配置中设置非空 token 并保存"
                "（保存后自动启动，无需重启）；未启动期间本地客户端无法连接"
            )
            return
        try:
            await self._hub.start(cfg.ws_host, cfg.ws_port, cfg.ws_token, cfg.max_frame_mb * 1024 * 1024)
        except Exception as e:
            # 端口被占用等启动失败同样不让插件加载失败，修复配置后保存即可自动重试
            self.ctx.logger.error(
                "WebSocket 服务启动失败: %s（检查端口 %s 是否被占用）；"
                "修复配置后保存即可自动重启服务", e, cfg.ws_port,
            )
            return
        self.ctx.logger.info("WebSocket 服务已启动: ws://%s:%d", cfg.ws_host, cfg.ws_port)

    # ===== 权限与辅助 =====

    def _spawn(self, coro) -> asyncio.Task:
        """启动后台任务（/sj 的处理），命令先返回，结果完成后另行发送。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _is_admin(self, user_id: str) -> bool:
        return str(user_id) in [str(a) for a in self.config.screenshot.admin_users]

    def _resolve_user_id(self, kwargs: dict, fallback: str = "") -> str:
        uid = kwargs.get("user_id") or kwargs.get("sender_id") or ""
        if not uid:
            msg = kwargs.get("message", {})
            if isinstance(msg, dict):
                uid = str(msg.get("user_id") or msg.get("sender", {}).get("user_id", ""))
        return uid or fallback

    def _policy_blur(self, user_id: str) -> int:
        """默认模糊策略：管理员截图不模糊，非管理员按配置半径模糊。"""
        return 0 if self._is_admin(user_id) else self.config.screenshot.blur_radius

    @staticmethod
    def _clamp_blur(blur: int) -> int:
        try:
            blur = int(blur)
        except (TypeError, ValueError):
            blur = 0
        return max(0, min(blur, MAX_BLUR_RADIUS))

    async def _capture(self, blur: int, user_id: str, timeout: float | None = 60.0) -> dict:
        """向本地端请求截图，返回统一形态 {"result": base64, "error": ""}。"""
        payload = protocol.make_request(
            str(uuid.uuid4()), "screenshot", {"blur_radius": blur}, user_id
        )
        return await self._hub.send_and_wait(payload, timeout)

    # ===== submit 回调与事件分发 =====

    async def _on_submit_response(self, call_id: str, response: dict, reply_api: str, call_ref: str) -> None:
        """submit 模式：把本地响应回发给提交方指定的 API。"""
        del call_id
        if not reply_api:
            return
        try:
            resp = await self.ctx.api.call(
                reply_api,
                call_ref=call_ref,
                result=response.get("result", ""),
                error=response.get("error", ""),
            )
        except Exception as e:
            self.ctx.logger.warning("submit 回调失败（api=%s）: %s", reply_api, e)
            return
        # 能力执行失败不抛异常而是返回 {"success": False}，必须显式检查（文档 §8.0）
        if isinstance(resp, dict) and resp.get("success") is False:
            self.ctx.logger.warning(
                "submit 回调返回失败（api=%s）: %s", reply_api, resp.get("error", "")
            )

    async def _on_event(self, event: str, data: dict) -> None:
        """本地上行事件：分发给所有订阅者。"""
        for api_name in self._hub.subscribers(event):
            task = asyncio.create_task(self._notify_subscriber(api_name, event, data))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    async def _notify_subscriber(self, api_name: str, event: str, data: dict) -> None:
        try:
            resp = await self.ctx.api.call(api_name, event=event, data=data)
        except Exception as e:
            self.ctx.logger.warning("事件分发失败（api=%s）: %s", api_name, e)
            return
        if isinstance(resp, dict) and resp.get("success") is False:
            self.ctx.logger.warning(
                "事件分发返回失败（api=%s）: %s", api_name, resp.get("error", "")
            )

    # ========================================================================
    # Command: /sj（/screenshot、/视奸）— 截图命令由本插件统一提供
    # ========================================================================

    @Command(
        "take_screenshot",
        description="对本地 PC 截图",
        pattern=r"^/(?:视奸|screenshot|sj)$",
    )
    async def cmd_screenshot(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """/视奸（/sj、/screenshot）：限流校验通过后转入后台任务处理，
        命令立即回复「正在截图」，避免大图响应慢导致命令卡住。"""
        user_id = self._resolve_user_id(kwargs, stream_id)

        limit_msg = self._rate_limiter.check(user_id)
        if limit_msg:
            await self.ctx.send.text(limit_msg, stream_id)
            return False, limit_msg, True

        if not self._hub.connected:
            await self.ctx.send.text("客户端未连接，请先启动本地客户端。", stream_id)
            return False, "客户端未连接", True

        blur = self._policy_blur(user_id)
        self._spawn(self._screenshot_worker(stream_id, user_id, blur))
        await self.ctx.send.text("正在截图，请稍候…", stream_id)
        return True, "截图处理中", True

    async def _screenshot_worker(self, stream_id: str, user_id: str, blur: int) -> None:
        """后台截图任务：保留 command_timeout 超时检测（截图不应无限等待）。"""
        try:
            resp = await self._capture(blur, user_id, self.config.screenshot.command_timeout)
            if resp.get("error"):
                await self.ctx.send.text(resp["error"], stream_id)
                return

            img_b64 = resp.get("result", "")
            if img_b64:
                try:
                    await self.ctx.send.hybrid(
                        [{"type": "image", "content": img_b64}],
                        stream_id,
                    )
                except Exception:
                    await self.ctx.send.text("截图已生成但发送失败。", stream_id)
            else:
                await self.ctx.send.text("截图失败：无图像数据。", stream_id)
        except Exception as e:
            await self.ctx.send.text(f"截图处理失败: {e}", stream_id)

    # ========================================================================
    # 公共 API（其他插件经 ctx.api.call("cateye.connect-hub.<name>", ...) 调用）
    # ========================================================================

    @API(
        "call",
        description="向本地端发送命令并同步等待响应，返回 {result, error}；timeout=None 表示不限时",
        version="1",
        public=True,
    )
    async def api_call(
        self,
        command: str = "",
        args: dict | None = None,
        user_id: str = "",
        timeout: float | None = 60.0,
        **kwargs: Any,
    ) -> dict:
        del kwargs
        payload = protocol.make_request(str(uuid.uuid4()), command, args, user_id)
        return await self._hub.send_and_wait(payload, timeout)

    @API(
        "submit",
        description="向本地端异步提交命令，立即返回 {success, call_id}；"
        "本地响应后回调 reply_api(call_ref=, result=, error=)，适合长耗时任务",
        version="1",
        public=True,
    )
    async def api_submit(
        self,
        command: str = "",
        args: dict | None = None,
        user_id: str = "",
        reply_api: str = "",
        call_ref: str = "",
        **kwargs: Any,
    ) -> dict:
        del kwargs
        err = self._hub.check_ready(command)
        if err:
            return {"success": False, "error": err, "call_id": ""}
        call_id = str(uuid.uuid4())
        payload = protocol.make_request(call_id, command, args, user_id)
        try:
            await self._hub.submit(payload, reply_api, call_ref)
        except Exception as e:
            return {"success": False, "error": str(e), "call_id": call_id}
        return {"success": True, "call_id": call_id, "error": ""}

    @API(
        "screenshot",
        description="获取本地 PC 截图，返回 {success, image_base64, error}；"
        "blur 缺省时按策略处理（管理员不模糊，非管理员按配置半径），"
        "user_id 传入 QQ 号用于策略判定",
        version="1",
        public=True,
    )
    async def api_screenshot(
        self,
        user_id: str = "",
        blur: int | None = None,
        **kwargs: Any,
    ) -> dict:
        del kwargs
        err = self._hub.check_ready("screenshot")
        if err:
            return {"success": False, "image_base64": "", "error": err}
        eff_blur = self._policy_blur(str(user_id or "")) if blur is None else self._clamp_blur(blur)
        resp = await self._capture(eff_blur, str(user_id or ""), self.config.screenshot.command_timeout)
        if resp.get("error"):
            return {"success": False, "image_base64": "", "error": resp["error"]}
        return {"success": True, "image_base64": resp.get("result", ""), "error": ""}

    @API("status", description="查询本地连接状态与已注册插件", version="1", public=True)
    async def api_status(self, **kwargs: Any) -> dict:
        del kwargs
        return {
            "connected": self._hub.connected,
            "client": self._hub.client_name,
            "plugins": self._hub.registry,
            "known_commands": sorted(self._hub.known_commands),
        }

    @API(
        "subscribe",
        description="订阅本地端事件：本地插件 emit_event(event, data) 后回调 api_name(event=, data=)",
        version="1",
        public=True,
    )
    async def api_subscribe(self, event: str = "", api_name: str = "", **kwargs: Any) -> dict:
        del kwargs
        if not event or not api_name:
            return {"success": False, "error": "event 与 api_name 均必填"}
        self._hub.subscribe(event, api_name)
        return {"success": True}

    @API("unsubscribe", description="取消事件订阅", version="1", public=True)
    async def api_unsubscribe(self, event: str = "", api_name: str = "", **kwargs: Any) -> dict:
        del kwargs
        self._hub.unsubscribe(event, api_name)
        return {"success": True}


def create_plugin() -> CateyeConnectHubPlugin:
    return CateyeConnectHubPlugin()
