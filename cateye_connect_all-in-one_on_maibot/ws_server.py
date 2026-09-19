"""WebSocket 服务端与连接管理（cateye.connect-hub 专用）。

单客户端模型（新连接顶掉旧连接，沿用旧版行为）：
  - 鉴权：第一条消息必须是 auth 帧，token 不匹配即断开（compare_digest 防时序侧信道）
  - call 模式：call_id 关联请求/响应（send_and_wait，支持超时或无限等待）
  - submit 模式：响应到达后经 on_response 回调（reply_api + call_ref），不占连接等待；
    连接断开/服务停止时，在途 submit 以断连错误触发回调（不静默丢弃）
  - 事件：本地主动上行的 event 帧经 on_event 回调分发

在途条目（pending/callbacks）登记时记录所属连接，断连清理只唤醒属于
该连接的条目——新连接顶掉旧连接后，旧连接的收尾清理不会误伤新连接
的状态，也不会漏唤醒旧连接的在途调用。
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any, Awaitable, Callable

import websockets

from . import protocol

# 响应回调：async (call_id, response, reply_api, call_ref)
ResponseCallback = Callable[[str, dict, str, str], Awaitable[None]]
# 事件回调：async (event, data)
EventCallback = Callable[[str, dict], Awaitable[None]]


class ConnectionHub:
    """与本地框架之间的唯一连接枢纽。"""

    def __init__(
        self,
        logger,
        auth_timeout: float = 10.0,
        on_response: ResponseCallback | None = None,
        on_event: EventCallback | None = None,
    ):
        self._logger = logger
        self._auth_timeout = auth_timeout
        self._on_response = on_response
        self._on_event = on_event

        self._token = ""
        self._server = None
        self._client: Any = None
        self._client_info: dict = {}
        # call_id -> (future, 所属连接)；断连时按连接归属唤醒
        self._pending: dict[str, tuple[asyncio.Future, Any]] = {}
        # call_id -> (reply_api, call_ref, 所属连接)
        self._callbacks: dict[str, tuple[str, str, Any]] = {}
        self._callback_tasks: set[asyncio.Task] = set()
        self._subscriptions: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    # ======================================================================
    # 状态
    # ======================================================================

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def client_name(self) -> str:
        return str(self._client_info.get("client", ""))

    @property
    def registry(self) -> list[dict]:
        """本地端握手时上报的插件注册表。"""
        return list(self._client_info.get("plugins", []))

    @property
    def known_commands(self) -> set[str]:
        cmds: set[str] = set()
        for p in self._client_info.get("plugins", []):
            cmds.update(str(c) for c in p.get("commands", []) if c)
        return cmds

    # ======================================================================
    # 服务生命周期
    # ======================================================================

    async def start(self, host: str, port: int, token: str, max_size: int) -> None:
        if not token:
            raise ValueError(
                "ws_token 未设置：为防止未鉴权访问本地 PC，必须在统一连接插件配置中设置非空 token"
            )
        self._token = token
        self._server = await websockets.serve(self._handler, host, port, max_size=max_size)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.clear_client()

    # ======================================================================
    # 连接管理
    # ======================================================================

    async def clear_client(self, ws=None) -> None:
        """连接断开清理：唤醒在途等待者，submit 回调以断连错误触发。

        - ws=None：无条件清理（stop 或当前连接断开），唤醒全部在途调用。
        - ws=旧连接：新连接顶掉旧连接后旧 handler 的收尾——只唤醒属于
          旧连接的在途调用，不得误清当前连接的状态。
        """
        async with self._lock:
            if ws is not None and self._client is not ws:
                self._fail_pending(ws)
                return
            self._client = None
            self._client_info = {}
            self._fail_pending(None)

    def _fail_pending(self, ws) -> None:
        """唤醒属于 ws（None=全部）的等待者；submit 回调以断连错误触发。"""
        for call_id, (fut, entry_ws) in list(self._pending.items()):
            if ws is not None and entry_ws is not ws:
                continue
            if not fut.done():
                fut.set_exception(ConnectionError("客户端连接失败，本地 PC 未响应"))
            self._pending.pop(call_id, None)
        for call_id, (reply_api, call_ref, _entry_ws) in list(self._callbacks.items()):
            if ws is not None and _entry_ws is not ws:
                continue
            self._callbacks.pop(call_id, None)
            if self._on_response:
                self._spawn_callback(
                    call_id,
                    {"result": "", "error": protocol.ERR_DISCONNECTED},
                    reply_api,
                    call_ref,
                )

    def _spawn_callback(self, call_id: str, response: dict, reply_api: str, call_ref: str) -> None:
        """异步执行响应回调；持有任务引用避免被 GC 中途回收。"""
        if not (self._on_response and reply_api):
            return
        task = asyncio.create_task(self._safe_on_response(call_id, response, reply_api, call_ref))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    async def _set_client(self, ws, info: dict) -> None:
        """登记新连接；关闭旧连接的 IO 放在锁外，避免半死连接拖住锁。"""
        async with self._lock:
            old = self._client
            self._client = ws
            self._client_info = info
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass

    # ======================================================================
    # WS 服务
    # ======================================================================

    async def _reject(self, ws, error: str) -> None:
        try:
            await ws.send(protocol.encode({"type": protocol.AUTH_ERROR, "error": error}))
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass

    async def _handler(self, ws) -> None:
        # 鉴权：第一条消息必须是 auth 帧
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self._auth_timeout)
            frame = json.loads(raw)
        except asyncio.TimeoutError:
            await ws.close()
            return
        except websockets.exceptions.ConnectionClosed:
            return
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            await self._reject(ws, f"鉴权失败：非法鉴权帧 ({e})")
            return

        if not isinstance(frame, dict):
            await self._reject(ws, "鉴权失败：非法鉴权帧")
            return
        token = frame.get("token")
        # compare_digest 走 bytes：防时序侧信道，且支持非 ASCII token
        token_ok = isinstance(token, str) and hmac.compare_digest(
            token.encode("utf-8"), self._token.encode("utf-8")
        )
        if frame.get("type") != protocol.AUTH or not token_ok:
            await self._reject(ws, "鉴权失败：token 不正确")
            return
        if frame.get("version") != protocol.PROTOCOL_VERSION:
            self._logger.warning(
                "客户端协议版本异常: %s（服务端 v%s）",
                frame.get("version"), protocol.PROTOCOL_VERSION,
            )

        await ws.send(
            protocol.encode(
                {
                    "type": protocol.AUTH_OK,
                    "version": protocol.PROTOCOL_VERSION,
                    "server": protocol.SERVER_NAME,
                }
            )
        )
        info = {
            "client": str(frame.get("client", "")),
            "plugins": frame.get("plugins") or [],
        }
        plugin_names = ", ".join(str(p.get("name", "?")) for p in info["plugins"]) or "无"
        self._logger.info("本地客户端已连接: %s（插件: %s）", info["client"] or "unknown", plugin_names)
        await self._set_client(ws, info)

        try:
            async for raw in ws:
                await self._handle_frame(raw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.warning("连接处理异常: %s", e)
        finally:
            self._logger.info("本地客户端已断开")
            await self.clear_client(ws)

    async def _handle_frame(self, raw) -> None:
        try:
            data = protocol.decode(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return

        ftype = protocol.frame_type_of(data)
        if ftype == protocol.RESPONSE:
            self._resolve(data)
        elif ftype == protocol.EVENT:
            event = str(data.get("event", ""))
            if event and self._on_event:
                try:
                    await self._on_event(event, data.get("data") or {})
                except Exception as e:
                    self._logger.warning("事件分发失败: %s", e)
        else:
            self._logger.warning("收到未知帧类型: %s", ftype or "(空)")

    def _resolve(self, data: dict) -> None:
        """响应帧：唤醒 send_and_wait 等待者，并触发 submit 回调。"""
        call_id = str(data.get("call_id", ""))
        if not call_id:
            return
        fut = self._pending.get(call_id)
        if fut is not None:
            entry_fut, _entry_ws = fut
            if not entry_fut.done():
                entry_fut.set_result(data)
            self._pending.pop(call_id, None)

        cb = self._callbacks.pop(call_id, None)
        if cb:
            reply_api, call_ref, _entry_ws = cb
            self._spawn_callback(call_id, data, reply_api, call_ref)

    async def _safe_on_response(self, call_id: str, data: dict, reply_api: str, call_ref: str) -> None:
        try:
            await self._on_response(call_id, data, reply_api, call_ref)
        except Exception as e:
            self._logger.warning("submit 响应回调异常: %s", e)

    # ======================================================================
    # 发送
    # ======================================================================

    def check_ready(self, command: str) -> str | None:
        """发送前检查，返回错误文案或 None。"""
        if not self._client:
            return protocol.ERR_NOT_CONNECTED
        command = str(command or "")
        if not command:
            return protocol.ERR_EMPTY_COMMAND
        if command not in self.known_commands:
            return protocol.ERR_UNKNOWN_COMMAND.format(command=command)
        return None

    async def send_and_wait(self, payload: dict, timeout: float | None = 60.0) -> dict:
        """发送命令并同步等待响应；timeout=None 表示不限时（长耗时任务用 submit）。

        返回统一形态 {"result": str, "error": str}。
        """
        err = self.check_ready(payload.get("command", ""))
        if err:
            return {"result": "", "error": err}

        call_id = str(payload.get("call_id", ""))
        ws = self._client
        if call_id in self._pending:
            return {"result": "", "error": f"重复的 call_id: {call_id}"}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[call_id] = (fut, ws)
        try:
            await ws.send(protocol.encode(payload))
            response = await asyncio.wait_for(fut, timeout=timeout)
            # str 归一化：防御异常端点发来非字符串字段，保证下游 send 可用
            return {
                "result": str(response.get("result", "") or ""),
                "error": str(response.get("error", "") or ""),
            }
        except asyncio.TimeoutError:
            return {"result": "", "error": protocol.ERR_TIMEOUT}
        except websockets.exceptions.ConnectionClosed:
            return {"result": "", "error": protocol.ERR_DISCONNECTED}
        except ConnectionError as e:
            return {"result": "", "error": str(e)}
        except Exception as e:
            return {"result": "", "error": str(e)}
        finally:
            self._pending.pop(call_id, None)

    async def submit(self, payload: dict, reply_api: str, call_ref: str) -> None:
        """异步提交：立即返回，响应到达后经 on_response 回调。

        连接断开时回调以 {"result": "", "error": ERR_DISCONNECTED} 触发。
        """
        call_id = str(payload.get("call_id", ""))
        ws = self._client
        if not ws:
            raise ConnectionError(protocol.ERR_NOT_CONNECTED)
        self._callbacks[call_id] = (reply_api, call_ref, ws)
        try:
            await ws.send(protocol.encode(payload))
        except Exception:
            self._callbacks.pop(call_id, None)
            raise

    # ======================================================================
    # 事件订阅
    # ======================================================================

    def subscribe(self, event: str, api_name: str) -> None:
        self._subscriptions.setdefault(event, set()).add(api_name)

    def unsubscribe(self, event: str, api_name: str) -> None:
        subs = self._subscriptions.get(event)
        if subs:
            subs.discard(api_name)
            if not subs:
                self._subscriptions.pop(event, None)

    def subscribers(self, event: str) -> list[str]:
        return sorted(self._subscriptions.get(event, ()))

    def adopt_subscriptions(self, other: "ConnectionHub") -> None:
        """配置热更新重建实例时继承事件订阅表（订阅由 MaiBot 侧插件登记，不应丢失）。"""
        self._subscriptions = {k: set(v) for k, v in other._subscriptions.items()}
