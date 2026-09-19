"""Cateye Connect 协议 v2 — 常量与帧工具（本地端副本）。

协议完整规范见 cateye_connect_all-in-one/docs/protocol-v2.md。
两端各自持有一份等价实现，帧结构以文档为准。
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


def encode(frame: dict) -> str:
    """帧 → JSON 文本（UTF-8、不转义中文）。"""
    return json.dumps(frame, ensure_ascii=False)


def decode(raw) -> dict:
    """JSON 文本 → 帧 dict。"""
    return json.loads(raw)


def is_request(data: dict) -> bool:
    """判定命令请求帧；无 type 但带 call_id+command 的旧版帧也按 request 处理。"""
    ftype = data.get("type")
    if ftype == REQUEST:
        return True
    return not ftype and bool(data.get("call_id")) and "command" in data
