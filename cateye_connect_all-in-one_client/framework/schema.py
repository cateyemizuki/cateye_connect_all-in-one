"""WebUI 配置 schema：字段描述格式、框架配置 schema、类型矫正与合并工具。

字段描述格式（借鉴 MaiBot 插件 ConfigField 的思路，精简为纯 dict）：
  {"key": "ws_tls.insecure", "type": "bool", "label": "...", "description": "...",
   "default": ..., "secret": False, "min": None, "max": None, "options": None,
   "placeholder": "", "restart": False}
  - key 支持 dot 路径定位嵌套字段
  - type: string / bool / int / list / enum
  - secret: 前端用密码框渲染；save 后端不做特殊处理
  - restart: True 表示修改后需重启框架才生效
schema 顶层为分组列表：[{"title": "连接", "fields": [...]}, ...]
"""

from __future__ import annotations

import copy
import re
from typing import Any

_SPLIT_RE = re.compile(r"[,，]")


def field(key: str, label: str, type_: str = "string", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": type_, **kw}


# ============================================================================
# 框架自身配置 schema（config.json）
# ============================================================================

FRAMEWORK_SCHEMA: list[dict] = [
    {
        "title": "连接",
        "fields": [
            field("ws_url", "服务端地址", placeholder="wss://服务器IP:8765",
                  description="统一连接插件（MaiBot 端）的地址；wss:// 前缀启用 TLS。修改保存后自动重连"),
            field("token", "鉴权 Token", secret=True,
                  description="与统一连接插件配置的 ws_token 一致；修改保存后自动重连"),
            field("reconnect_interval", "重连间隔（秒）", "int", min=1, max=600),
            field("auth_timeout", "鉴权等待（秒）", "int", min=3, max=120),
            field("ws_tls.insecure", "跳过 TLS 证书校验", "bool",
                  description="自签名/IP 证书场景"),
            field("ws_tls.ca_file", "自定义 CA 证书路径",
                  description="用于校验自签名服务器证书"),
        ],
    },
    {
        "title": "插件",
        "fields": [
            field("plugins_dir", "插件目录", restart=True,
                  description="插件加载目录（相对框架根解析）；修改后需重启框架"),
        ],
    },
    {
        "title": "WebUI",
        "fields": [
            field("webui.enabled", "启用 WebUI", "bool", restart=True),
            field("webui.host", "监听地址", restart=True,
                  description="默认 127.0.0.1 仅本机访问；0.0.0.0 时必须设置访问密码"),
            field("webui.port", "端口", "int", min=1, max=65535, restart=True),
            field("webui.password", "访问密码", secret=True,
                  description="留空表示无密码（仅允许监听在回环地址时）；修改后需重新登录"),
        ],
    },
]

# 修改后需要断开重连即可生效的键（点路径）
RECONNECT_KEYS = ("ws_url", "token", "ws_tls.insecure", "ws_tls.ca_file")


def iter_fields(schema: list[dict]):
    for group in schema or []:
        for f in group.get("fields", []):
            yield f


# ============================================================================
# dot 路径与合并工具
# ============================================================================

def get_by_path(data: dict, key: str, default: Any = None) -> Any:
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_by_path(data: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def deep_merge(base: dict, patch: dict) -> dict:
    """递归合并 patch 到 base 副本上（patch 优先），返回新 dict。"""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def coerce_value(value: Any, f: dict) -> Any:
    """按字段描述矫正类型/范围（WebUI 表单传来的值可能是字符串等）。"""
    ftype = f.get("type", "string")
    if ftype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是")
        return bool(value)
    if ftype == "int":
        try:
            num = int(float(value))
        except (TypeError, ValueError):
            num = int(f["default"]) if f.get("default") is not None else 0
        if f.get("min") is not None:
            num = max(int(f["min"]), num)
        if f.get("max") is not None:
            num = min(int(f["max"]), num)
        return num
    if ftype == "list":
        if isinstance(value, str):
            value = [p.strip() for p in _SPLIT_RE.split(value) if p.strip()]
        return list(value) if isinstance(value, list) else []
    if ftype == "enum":
        options = f.get("options") or []
        s = str(value)
        return s if s in options else (f.get("default") or options[0] if options else s)
    return "" if value is None else str(value)


def apply_patch(existing: dict, patch: dict, schema: list[dict] | None) -> dict:
    """把 patch 合并进 existing（不动 existing 本身）。

    - schema 声明的键：从 patch 取值并按描述矫正类型/范围
    - 其余键：deep_merge 透传（保留用户手工加入的未知字段）
    """
    merged = copy.deepcopy(existing)
    patch = copy.deepcopy(patch)

    covered: set[str] = set()
    for f in iter_fields(schema):
        key = f["key"]
        covered.add(key)
        if get_by_path(patch, key) is None:
            continue
        set_by_path(merged, key, coerce_value(get_by_path(patch, key), f))

    # patch 中未被 schema 覆盖的顶层键透传合并（保留未知字段）
    top_patch = {k: v for k, v in patch.items() if k not in {key.split(".")[0] for key in covered}}
    if top_patch:
        merged = deep_merge(merged, top_patch)
    return merged


def schema_from_values(values: dict) -> list[dict]:
    """插件未声明 config_schema 时，从当前配置值反推一个通用 schema。"""
    groups: list[dict] = []
    current: dict[str, list] = {}

    def type_of(v: Any) -> str:
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int) and not isinstance(v, bool):
            return "int"
        if isinstance(v, list):
            return "list"
        return "string"

    def add(key: str, v: Any) -> None:
        if isinstance(v, dict):
            for k2, v2 in v.items():
                add(f"{key}.{k2}" if key else k2, v2)
            return
        current.setdefault("配置", []).append(field(key, key, type_of(v), default=v))

    for k, v in values.items():
        add(k, v)
    for title, fields in current.items():
        groups.append({"title": title, "fields": fields})
    return groups
