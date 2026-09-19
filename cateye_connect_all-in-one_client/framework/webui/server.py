"""WebUI 服务：本地框架与插件的配置编辑界面。

设计参考 MaiBot WebUI（FastAPI + schema 驱动表单 + 保存即写盘 + 热重载回调），
实现上做了精简：aiohttp 与框架同事件循环运行（无独立线程）、单文件原生前端、
单密码 token + HttpOnly Cookie 鉴权。

端点：
  GET  /                              单页前端
  POST /api/auth/verify               登录 {password} → 设置 Cookie
  POST /api/auth/logout               登出
  GET  /api/status                    连接状态 + 插件列表
  GET  /api/config/framework          框架配置 {schema, config}
  POST /api/config/framework          保存框架配置（合并/矫正/备份写盘/自动重连）
  GET  /api/config/plugin/{name}      插件配置 {schema, config, file}
  POST /api/config/plugin/{name}      保存插件配置（写盘 + on_config_update 热更新）
  POST /api/action/reconnect          手动断开重连
"""

from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import FRAMEWORK_VERSION
from .. import autostart
from .. import schema as S
from ..config import CLIENT_ROOT, backup_file, save_config

COOKIE_NAME = "cateye_webui"
STATIC_DIR = Path(__file__).resolve().parent / "static"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WebUIServer:
    """与框架同事件循环运行的配置 WebUI。"""

    def __init__(self, config: dict, client, router, log):
        self._config = config  # 与 main/ws_client 共享的同一 dict
        self._client = client
        self._router = router
        self._log = log
        self._runner: web.AppRunner | None = None

    # ======================================================================
    # 生命周期
    # ======================================================================

    async def start(self) -> None:
        cfg = self._webui_cfg()
        host = str(cfg.get("host", "127.0.0.1"))
        port = int(cfg.get("port", 8766))
        password = str(cfg.get("password", "") or "")
        if not password and host not in _LOOPBACK_HOSTS:
            raise ValueError("WebUI 密码为空时仅允许监听回环地址；请设置 webui.password")

        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_post("/api/auth/verify", self._auth_verify)
        app.router.add_post("/api/auth/logout", self._auth_logout)
        app.router.add_get("/api/status", self._status)
        app.router.add_get("/api/config/framework", self._framework_get)
        app.router.add_post("/api/config/framework", self._framework_save)
        app.router.add_get("/api/config/plugin/{name}", self._plugin_get)
        app.router.add_post("/api/config/plugin/{name}", self._plugin_save)
        app.router.add_post("/api/action/reconnect", self._action_reconnect)
        app.router.add_post("/api/action/autostart", self._action_autostart)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        self._log.info("WebUI 已启动: http://%s:%d（密码见 config.json 的 webui.password）", host, port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _webui_cfg(self) -> dict:
        cfg = self._config.get("webui")
        return cfg if isinstance(cfg, dict) else {}

    # ======================================================================
    # 鉴权
    # ======================================================================

    def _password(self) -> str:
        return str(self._webui_cfg().get("password", "") or "")

    def _authed(self, request: web.Request) -> bool:
        password = self._password()
        if not password:
            return True  # 仅回环监听时才会走到这里
        cookie = request.cookies.get(COOKIE_NAME, "")
        if cookie and self._safe_eq(cookie, password):
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and self._safe_eq(auth[7:], password):
            return True
        return False

    @staticmethod
    def _safe_eq(a: str, b: str) -> bool:
        try:
            return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
        except Exception:
            return False

    def _guard(self, request: web.Request) -> web.Response | None:
        if self._authed(request):
            return None
        return web.json_response({"success": False, "error": "未登录或密码已变更"}, status=401)

    async def _auth_verify(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if self._safe_eq(str(body.get("password", "")), self._password()):
            resp = web.json_response({"success": True})
            resp.set_cookie(
                COOKIE_NAME, self._password(),
                max_age=7 * 24 * 3600, httponly=True, samesite="Lax", path="/",
            )
            return resp
        return web.json_response({"success": False, "error": "密码错误"}, status=401)

    async def _auth_logout(self, request: web.Request) -> web.Response:
        resp = web.json_response({"success": True})
        resp.del_cookie(COOKIE_NAME, path="/")
        return resp

    # ======================================================================
    # 状态
    # ======================================================================

    async def _index(self, request: web.Request) -> web.FileResponse:
        del request
        return web.FileResponse(STATIC_DIR / "index.html")

    def _autostart_state(self) -> bool | None:
        """开机自启动状态；None 表示当前环境不支持（如非 Windows）。"""
        try:
            return autostart.is_enabled()
        except Exception:
            return None

    async def _status(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        cfg = self._webui_cfg()
        return web.json_response({
            "framework_version": FRAMEWORK_VERSION,
            "connected": self._client.connected if self._client else False,
            "ws_url": self._config.get("ws_url", ""),
            "plugins": self._router.registry if self._router else [],
            "autostart": self._autostart_state(),
            "webui": {
                "host": cfg.get("host", "127.0.0.1"),
                "port": cfg.get("port", 8766),
                "has_password": bool(self._password()),
            },
        })

    # ======================================================================
    # 框架配置
    # ======================================================================

    async def _framework_get(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        return web.json_response({
            "schema": S.FRAMEWORK_SCHEMA,
            "config": self._config,
            "file": str(CLIENT_ROOT / "config.json"),
        })

    async def _framework_save(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        try:
            body = await request.json()
            patch = body.get("config")
            if not isinstance(patch, dict):
                raise ValueError("请求体缺少 config 对象")
        except Exception as e:
            return web.json_response({"success": False, "error": f"请求体无效: {e}"}, status=400)

        all_keys = [f["key"] for f in S.iter_fields(S.FRAMEWORK_SCHEMA)]
        before = {k: S.get_by_path(self._config, k) for k in all_keys}
        merged = S.apply_patch(self._config, patch, S.FRAMEWORK_SCHEMA)

        # 安全约束：监听非回环地址时必须设置访问密码
        webui = merged.get("webui") or {}
        if not str(webui.get("password", "") or "") and str(
            webui.get("host", "127.0.0.1")
        ) not in _LOOPBACK_HOSTS:
            return web.json_response({
                "success": False,
                "error": "监听非回环地址时必须设置 WebUI 访问密码",
            }, status=400)

        after = {k: S.get_by_path(merged, k) for k in all_keys}
        reconnect_needed = any(
            k in S.RECONNECT_KEYS and str(before[k]) != str(after[k]) for k in all_keys
        )

        self._config.clear()
        self._config.update(merged)  # 就地更新共享配置
        save_config(self._config)

        reconnected = False
        if reconnect_needed:
            await self._client.request_reconnect()
            reconnected = True

        restart_required = [
            f.get("label") or f["key"]
            for f in S.iter_fields(S.FRAMEWORK_SCHEMA)
            if f.get("restart") and str(before[f["key"]]) != str(after[f["key"]])
        ]

        return web.json_response({
            "success": True,
            "reconnected": reconnected,
            "restart_required": restart_required,
        })

    # ======================================================================
    # 插件配置
    # ======================================================================

    def _find_plugin(self, name: str):
        for p in (self._router.plugins if self._router else []):
            if p.name == name:
                return p
        return None
    async def _plugin_get(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        plugin = self._find_plugin(request.match_info["name"])
        if plugin is None:
            return web.json_response({"success": False, "error": "插件不存在"}, status=404)

        manifest = {}
        manifest_path = plugin.ctx.plugin_dir / "plugin.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        schema = manifest.get("config_schema") or S.schema_from_values(plugin.config or {})
        declared = bool(manifest.get("config_schema"))
        return web.json_response({
            "schema": schema,
            "schema_declared": declared,
            "config": plugin.config or {},
            "file": str(plugin.ctx.plugin_dir / "config.json"),
        })

    async def _plugin_save(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        plugin = self._find_plugin(request.match_info["name"])
        if plugin is None:
            return web.json_response({"success": False, "error": "插件不存在"}, status=404)
        try:
            body = await request.json()
            patch = body.get("config")
            if not isinstance(patch, dict):
                raise ValueError("请求体缺少 config 对象")
        except Exception as e:
            return web.json_response({"success": False, "error": f"请求体无效: {e}"}, status=400)

        manifest_path = plugin.ctx.plugin_dir / "plugin.json"
        schema = None
        if manifest_path.is_file():
            try:
                schema = json.loads(manifest_path.read_text(encoding="utf-8")).get("config_schema")
            except Exception:
                schema = None

        merged = S.apply_patch(plugin.config or {}, patch, schema)
        config_file = plugin.ctx.plugin_dir / "config.json"
        backup_file(config_file)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

        hot_applied = False
        try:
            hot_applied = bool(await plugin.on_config_update(merged))
        except Exception as e:
            self._log.error("插件 %s 配置热更新失败: %s", plugin.name, e)
            return web.json_response({
                "success": True,
                "hot_applied": False,
                "error": f"已写入文件，但热更新失败: {e}",
            })
        return web.json_response({
            "success": True,
            "hot_applied": hot_applied,
            "error": "" if hot_applied else "已写入文件；插件未实现热更新，重启框架后生效",
        })

    # ======================================================================
    # 操作
    # ======================================================================

    async def _action_reconnect(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        await self._client.request_reconnect()
        return web.json_response({"success": True})

    async def _action_autostart(self, request: web.Request) -> web.Response:
        if (resp := self._guard(request)) is not None:
            return resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        enable = bool(body.get("enabled"))
        try:
            # PowerShell 子进程是阻塞调用，放线程池避免卡住事件循环
            if enable:
                await asyncio.to_thread(autostart.install)
            else:
                await asyncio.to_thread(autostart.uninstall)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)
        return web.json_response({"success": True, "enabled": self._autostart_state()})
