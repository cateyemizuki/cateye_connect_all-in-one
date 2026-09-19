"""统一连接插件（cateye.connect-hub）调用封装。

hub 公共 API 全名：
  cateye.connect-hub.call      同步命令（返回 {"result", "error"}）
  cateye.connect-hub.submit    异步提交（返回 {"success", "call_id"}，响应走回调）
  cateye.connect-hub.status    连接状态
RPC 层失败时 ctx.api.call 返回 {"success": False, "error": ...}，
此处统一归一化，业务代码只处理两种形态：
  call    → {"result": str, "error": str}
  submit  → {"success": bool, "error": str, "call_id": str}
"""

HUB_PLUGIN_ID = "cateye.connect-hub"
HUB_API_CALL = f"{HUB_PLUGIN_ID}.call"
HUB_API_SUBMIT = f"{HUB_PLUGIN_ID}.submit"
HUB_API_STATUS = f"{HUB_PLUGIN_ID}.status"

# 本插件动态 API 名（on_load 注册；hub.submit 的响应回调打到这）
ON_RESULT_API = "on_result"


def _normalize_call(resp) -> dict:
    """hub.call 结果归一化为 {"result", "error"}。"""
    if isinstance(resp, dict) and "result" in resp:
        return {"result": resp.get("result", ""), "error": resp.get("error", "")}
    if isinstance(resp, dict):
        return {"result": "", "error": str(resp.get("error") or resp)}
    return {"result": "", "error": f"统一连接插件返回异常: {resp!r}"}


def _normalize_submit(resp) -> dict:
    """hub.submit 结果归一化为 {"success", "error", "call_id"}。"""
    if isinstance(resp, dict) and "success" in resp:
        return {
            "success": bool(resp.get("success")),
            "error": resp.get("error", ""),
            "call_id": resp.get("call_id", ""),
        }
    if isinstance(resp, dict):
        return {"success": False, "error": str(resp.get("error") or resp), "call_id": ""}
    return {"success": False, "error": f"统一连接插件返回异常: {resp!r}", "call_id": ""}


class HubClient:
    """对统一连接插件公共 API 的轻封装。"""

    def __init__(self, plugin):
        self._plugin = plugin

    async def call(
        self,
        command: str,
        args: dict | None = None,
        user_id: str = "",
        timeout: float | None = 60.0,
    ) -> dict:
        """同步发送命令并等待本地响应；timeout=None 表示不限时。"""
        resp = await self._plugin.ctx.api.call(
            HUB_API_CALL,
            command=command,
            args=args or {},
            user_id=str(user_id or ""),
            timeout=timeout,
        )
        return _normalize_call(resp)

    async def submit(
        self,
        command: str,
        args: dict | None = None,
        user_id: str = "",
        reply_api: str = "",
        call_ref: str = "",
    ) -> dict:
        """异步提交长耗时命令；本地响应后 hub 回调 reply_api(call_ref=, result=, error=)。"""
        resp = await self._plugin.ctx.api.call(
            HUB_API_SUBMIT,
            command=command,
            args=args or {},
            user_id=str(user_id or ""),
            reply_api=reply_api,
            call_ref=call_ref,
        )
        return _normalize_submit(resp)

    async def is_connected(self) -> bool:
        """本地框架是否在线（用于命令的快速预检）。"""
        try:
            resp = await self._plugin.ctx.api.call(HUB_API_STATUS)
        except Exception:
            return False
        return bool(isinstance(resp, dict) and resp.get("connected"))
