# 指南：开发 MaiBot 侧业务插件（经统一连接插件通信）

MaiBot 侧的**连接职责全部归统一连接插件 `cateye.connect-hub`**。
你的业务插件不建任何连接，只通过它的公共 API 与本地端收发数据。

## 1. Manifest 声明

`_manifest.json` 中：

```json
{
  "dependencies": [
    {"type": "plugin", "id": "cateye.connect-hub", "version_spec": ">=1.0.0"}
  ],
  "capabilities": [
    "send.text", "send.forward", "send.hybrid",
    "api.call"
  ]
}
```

- plugin 依赖保证 hub 先于你的插件加载。
- `api.call` 用于调用 hub API（以及接收 submit 回调时被 hub 调用）。
- 发 QQ 消息按需声明 `send.*` 能力。

## 2. hub 公共 API（全名 `cateye.connect-hub.<name>`）

| API | 参数 | 返回 | 用途 |
|---|---|---|---|
| `call` | `command, args={}, user_id="", timeout=60.0` | `{"result": str, "error": str}` | 同步命令；`timeout=None` 表示不限时 |
| `submit` | `command, args={}, user_id="", reply_api="", call_ref=""` | `{"success": bool, "call_id": str, "error": str}` | 异步长任务；响应回调 `reply_api` |
| `screenshot` | `user_id="", blur=None` | `{"success": bool, "image_base64": str, "error": str}` | 获取本地 PC 截图（base64 PNG）。`blur` 缺省时按策略（管理员不模糊/非管理员配置半径），显式传 0-64 覆盖 |
| `status` | — | `{"connected": bool, "client": str, "plugins": [...], "known_commands": [...]}` | 连接预检/诊断 |
| `subscribe` | `event, api_name` | `{"success": bool}` | 订阅本地端上行事件 |
| `unsubscribe` | `event, api_name` | `{"success": bool}` | 取消订阅 |

调用方式：

```python
resp = await self.ctx.api.call(
    "cateye.connect-hub.call",
    command="screenshot", args={"blur_radius": 5},
    user_id="123456", timeout=60,
)
if resp.get("error"):
    ...   # 失败（含 未连接/超时/断开/未知命令）
else:
    img_b64 = resp["result"]
```

> RPC 层失败时 `ctx.api.call` 返回 `{"success": False, "error": ...}`（无 `result` 键）；
> 建议对返回值做一次归一化（把失败统一成 `{"success": False, "error": ...}`）再使用。

## 3. 长耗时任务：call 还是 submit？

`/dsh` 这类可能跑几十分钟的任务用 **submit**（避免占用一次 RPC 等待到底）：

```python
# on_load 里注册回调 API
self.register_dynamic_api("on_result", self._on_result, version="1", public=True)
await self.sync_dynamic_apis()

# 提交（call_ref 任意透传，回调时原样带回；发回 QQ 场景传 stream_id）
resp = await self.ctx.api.call(
    "cateye.connect-hub.submit",
    command="deepseek", args={"prompt": "...", "workspace": "test"},
    user_id=user_id,
    reply_api=f"<你的插件id>.on_result",
    call_ref=stream_id,
)
if not resp.get("success"):
    ...   # 未连接等即时错误

# 回调签名（hub 调用）
async def _on_result(self, call_ref: str = "", result: str = "", error: str = "", **kwargs):
    await self.ctx.send.text(result or error, call_ref)
    return {"success": True}
```

短命令（截图/CMD 等，秒级返回）直接 `call` + `timeout` 即可。

> 断连/服务重启时的 submit 语义：hub 会对该连接的所有在途 submit 以
> `error="客户端连接失败，本地 PC 已断开连接"` 触发一次回调（不会静默丢弃），
> 因此回调处理函数只需按普通错误处理即可；`call` 模式的等待者同样会被唤醒并返回该错误。

## 4. 订阅本地端事件（上行推送）

```python
await self.ctx.api.call(
    "cateye.connect-hub.subscribe",
    event="adb_status", api_name="<你的插件id>.on_adb_status",
)
# 本地插件 emit_event("adb_status", {...}) 时，hub 会回调：
async def on_adb_status(self, event: str = "", data: dict | None = None, **kwargs):
    ...
```

## 5. 命令名从哪来？

本地端插件在握手时上报命令注册表（例如某插件提供
`screenshot/cmd_start/cmd_stop` 等命令）。你的插件可调用
`status` API 查询 `known_commands`；发未知命令会得到
`本地未加载可处理「<命令>」的插件` 错误。

## 6. 业务责任边界

- **留在你的插件**：QQ 命令注册、admin/权限校验、限流、长文本拆分、`send.*` 发送。
- **hub 负责**：连接、鉴权、请求-响应关联、超时与断连错误文案、事件分发、
  截图（/sj 命令与 `screenshot` API，模糊策略与限流都在 hub 配置里）。
