"""框架配置加载与保存（config.json，与 main.py 同目录）。"""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import Any

CLIENT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = CLIENT_ROOT / "config.json"

CONFIG_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "ws_url": "ws://127.0.0.1:8765",
    "token": "",
    # wss 连接的证书校验：insecure=true 跳过校验；ca_file 指定自签名 CA
    "ws_tls": {
        "insecure": False,
        "ca_file": "",
    },
    "reconnect_interval": 5,
    "auth_timeout": 10,
    "plugins_dir": "./plugins",
    # 注意：webui.password 不写默认值——首次启动由 ensure_webui_password
    # 自动生成并写回；用户显式置空则表示无密码（仅允许回环监听）
    "webui": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8766,
    },
}


def load_config(path: Path | None = None) -> dict:
    """加载配置；文件缺失时写出默认配置，损坏时回退默认并告警。"""
    p = path or CONFIG_FILE
    cfg = dict(DEFAULT_CONFIG)
    if p.is_file():
        raw = None
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[!] 配置文件加载失败，使用默认配置: {e}")
        if isinstance(raw, dict):
            cfg.update(raw)
    else:
        _save(cfg, p)
    return cfg


def save_config(cfg: dict, path: Path | None = None) -> None:
    """保存配置并写一份 .bak 备份（覆盖上一份备份）。"""
    p = path or CONFIG_FILE
    backup_file(p)
    _save(cfg, p)


def backup_file(path: Path) -> Path | None:
    """滚动备份：把现有文件复制为 <名>.bak。"""
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            shutil.copyfile(path, bak)
            return bak
        except OSError:
            return None
    return None


def ensure_webui_password(config: dict) -> None:
    """首次启动生成 WebUI 访问密码并写回配置文件。

    password 键不存在 → 生成；显式置空 → 尊重用户（仅回环监听时允许无密码）。
    """
    webui = config.setdefault("webui", {})
    if webui.get("password") or "password" in webui:
        return
    webui["password"] = secrets.token_hex(16)
    _save(config, CONFIG_FILE)
    print(f"[i] 已生成 WebUI 访问密码: {webui['password']}（已写入 config.json）")


def _save(cfg: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
