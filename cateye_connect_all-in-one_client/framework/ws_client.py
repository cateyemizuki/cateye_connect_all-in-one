"""WS 客户端：连接、鉴权（上报插件注册表）、命令分发、事件上行、自动重连。

连接支持 ws:// 与 wss://：ws_url 前缀改为 wss:// 即启用 TLS。
证书校验默认走系统 CA；自签名/IP 证书可通过 ws_tls 配置段跳过校验或指定 CA。
"""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Optional

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

from . import FRAMEWORK_VERSION
from . import protocol
from .log import Logger
from .router import CommandRouter


class FrameworkClient:
    def __init__(self, config: dict, logger: Logger):
        self._config = config
        self._log = logger
        self._router: Optional[CommandRouter] = None
        self._ws = None
        self._cert_hint_shown = False

    def set_router(self, router: CommandRouter) -> None:
        self._router = router

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ======================================================================
    # 事件上行
    # ======================================================================

    async def emit_event(self, event: str, data: dict) -> bool:
        """本地插件向 MaiBot 端推送事件（未连接时返回 False）。"""
        ws = self._ws
        if not ws:
            self._log.warn("事件 %s 上行失败：未连接", event)
            return False
        try:
            await ws.send(protocol.encode({"type": protocol.EVENT, "event": event, "data": data}))
            return True
        except Exception as e:
            self._log.warn("事件 %s 上行失败: %s", event, e)
            return False

    # ======================================================================
    # 主循环
    # ======================================================================

    async def request_reconnect(self) -> None:
        """断开当前连接（自动重连循环会用最新配置重连）。未连接时无操作。"""
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def run_forever(self) -> None:
        if not HAS_WS:
            self._log.error("缺少 websockets 依赖（pip install websockets）")
            return
        if self._router is None:
            self._log.error("未设置路由（set_router），无法处理命令")
            return

        while True:
            try:
                # 每次重连都重新读配置：WebUI 修改连接参数后，断开重连即生效
                ws_url = str(self._config.get("ws_url", ""))
                token = str(self._config.get("token", ""))
                try:
                    interval = max(float(self._config.get("reconnect_interval", 5)), 1.0)
                except (TypeError, ValueError):
                    interval = 5.0
                ssl_ctx = build_ssl_context(self._config)
                if ssl_ctx is not None:
                    self._log.info("TLS 已启用: %s", describe_tls_mode(self._config))

                self._log.info("正在连接 %s ...", ws_url)
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    max_size=10 * 1024 * 1024,
                    ssl=ssl_ctx,
                ) as ws:
                    self._ws = ws
                    status = await self._auth(ws, token)
                    if status == "ok":
                        async for raw in ws:
                            await self._handle_frame(ws, raw)
                    elif status == "rejected":
                        # 仅明确被拒（token 错误）属配置错误，停止重连
                        self._ws = None
                        return
                    # retry（超时/响应异常）：瞬态故障，落到下方 sleep 后重连
            except ssl.SSLCertVerificationError as e:
                self._print_cert_hint()
                self._log.warn("证书校验失败: %s，%s 秒后重试...", e, interval)
            except (ConnectionRefusedError, OSError) as e:
                self._log.warn("连接失败: %s，%s 秒后重试...", e, interval)
            except Exception as e:
                self._log.warn("异常: %s，%s 秒后重试...", e, interval)
            finally:
                self._ws = None
            await asyncio.sleep(interval)

    async def _auth(self, ws, token: str) -> str:
        """鉴权：上报 token、协议版本与本地插件注册表。

        返回 "ok"（通过）/ "retry"（超时等瞬态故障，应重连）/ "rejected"（被拒，停止）。
        """
        await ws.send(
            protocol.encode(
                {
                    "type": protocol.AUTH,
                    "token": token,
                    "version": protocol.PROTOCOL_VERSION,
                    "client": f"{protocol.CLIENT_NAME}/{FRAMEWORK_VERSION}",
                    "plugins": self._router.registry if self._router else [],
                }
            )
        )
        try:
            timeout = float(self._config.get("auth_timeout", 10))
        except (TypeError, ValueError):
            timeout = 10.0
        try:
            resp = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            self._log.warn("鉴权超时（服务端 %s 秒未应答），将重试连接", timeout)
            return "retry"
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._log.warn("鉴权响应解析失败: %s，将重试连接", e)
            return "retry"
        if not isinstance(resp, dict):
            self._log.warn("鉴权响应格式异常，将重试连接")
            return "retry"
        if resp.get("type") == protocol.AUTH_OK:
            self._log.info("已连接并鉴权成功（服务端: %s）", resp.get("server", "?"))
            return "ok"
        self._log.error("鉴权失败: %s", resp.get("error", "未知错误"))
        return "rejected"

    async def _handle_frame(self, ws, raw) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw = raw.strip()
        if not raw:
            return
        try:
            frame = protocol.decode(raw)
        except json.JSONDecodeError:
            self._log.warn("收到非 JSON: %s", str(raw)[:80])
            return
        if not isinstance(frame, dict):
            return

        call_id = str(frame.get("call_id", ""))
        command = str(frame.get("command", ""))
        if protocol.is_request(frame):
            self._log.info("收到: %s (call_id=%s)", command, call_id[:8])
            response = await self._router.dispatch(
                call_id, command, frame.get("args") or {}, str(frame.get("user_id", ""))
            )
            await ws.send(protocol.encode({"type": protocol.RESPONSE, **response}))
            err = response.get("error")
            if err:
                self._log.error("命令异常: %s", err)
            else:
                self._log.info("已回复: call_id=%s", call_id[:8])
        else:
            self._log.warn("收到未知帧: %s", str(frame)[:120])

    def _print_cert_hint(self) -> None:
        if self._cert_hint_shown:
            return
        self._cert_hint_shown = True
        self._log.error("TLS 证书校验失败")
        print("    若为自签名/IP 证书，可在 config.json 中配置 ws_tls：")
        print('      "ws_tls": {"insecure": true}             跳过校验')
        print('      "ws_tls": {"ca_file": "服务器证书路径"}   信任指定 CA')
        print("    若证书为域名证书，请将 ws_url 中的 IP 换成证书对应的域名")


def build_ssl_context(config: dict) -> ssl.SSLContext | None:
    """ws_url 为 wss:// 时构造 TLS 上下文，ws:// 返回 None。"""
    if not str(config.get("ws_url", "")).lower().startswith("wss://"):
        return None
    tls_cfg = config.get("ws_tls") or {}
    if tls_cfg.get("insecure"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=tls_cfg.get("ca_file") or None)


def describe_tls_mode(config: dict) -> str:
    tls_cfg = config.get("ws_tls") or {}
    if tls_cfg.get("insecure"):
        return "跳过证书校验"
    if tls_cfg.get("ca_file"):
        return f"自定义 CA: {tls_cfg['ca_file']}"
    return "系统 CA 严格校验"
