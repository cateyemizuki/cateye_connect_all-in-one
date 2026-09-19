"""Cateye Connect 协议 v2 — 常量与帧工具。

协议完整规范见 cateye_connect_all-in-one/docs/protocol-v2.md。
两端（本插件与本地框架）各自持有一份等价实现，帧结构以文档为准。
"""

from __future__ import annotations

import json

PROTOCOL_VERSION = 2
SERVER_NAME = "cateye-connect-hub"
CLIENT_NAME = "cateye-connect-framework"

# ---- 帧类型 ----
AUTH = "auth"  # 本地 → hub：鉴权 + 插件注册表
AUTH_OK = "auth_ok"  # hub → 本地：鉴权通过
AUTH_ERROR = "auth_error"  # hub → 本地：鉴权失败（随后断开）
REQUEST = "request"  # hub → 本地：命令请求
RESPONSE = "response"  # 本地 → hub：命令响应
EVENT = "event"  # 本地 → hub：主动事件（上行推送）

# ---- 错误文案（与旧版 maibot-deepseek-harness-connect 保持一致） ----
ERR_NOT_CONNECTED = "客户端未连接，请先启动本地客户端"
ERR_TIMEOUT = "本地处理超时，请重试"
ERR_DISCONNECTED = "客户端连接失败，本地 PC 已断开连接"
ERR_UNKNOWN_COMMAND = "本地未加载可处理「{command}」的插件"
ERR_EMPTY_COMMAND = "缺少 command 参数"


def encode(frame: dict) -> str:
    """帧 → JSON 文本（UTF-8、不转义中文）。"""
    return json.dumps(frame, ensure_ascii=False)


def decode(raw) -> dict:
    """JSON 文本 → 帧 dict。"""
    return json.loads(raw)


def make_request(call_id: str, command: str, args: dict | None = None, user_id: str = "") -> dict:
    """构造下行命令帧。"""
    return {
        "type": REQUEST,
        "call_id": call_id,
        "command": command,
        "args": args if isinstance(args, dict) else {},
        "user_id": str(user_id or ""),
    }


def frame_type_of(data: dict) -> str:
    """判定帧类型；无 type 但带 call_id 的帧按 response 兼容处理（v1 旧帧）。"""
    ftype = data.get("type")
    if not ftype and data.get("call_id"):
        return RESPONSE
    return str(ftype or "")
