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
| `screenshot` | `user_id="", blur=None` | `{"success": bool, "image_base64": str, "screens": [...], "screen_count": int, "error": str}` | 获取本地 PC 截图（base64 PNG）。**多屏兼容**：`image_base64` 恒为主屏截图；`screens` 每屏一项（主屏恒在第 0 位），详见下文「多屏截图兼容」。`blur` 缺省时按策略（管理员不模糊/非管理员配置半径），显式传 0-64 覆盖 |
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

## 6. 多屏截图兼容（调用 screenshot API 必读）

本地 PC 可能接多台显示器（副屏常位于虚拟屏**负坐标区**）。自 v1.1.0 起，
本地端按**逐屏抓取**（每屏一个独立 base64 PNG，主屏优先），不再拼接整幅大图
——拼接在负坐标/混合分辨率布局下会尺寸错位、内容串屏（实测缺陷）。

### 6.1 返回形态

```json
{
  "success": true,
  "image_base64": "<主屏截图 base64 PNG>",
  "screens": [
    {"index": 0, "primary": true,  "left": 0,     "top": 0,    "width": 2560, "height": 1440, "image_base64": "<主屏 PNG>"},
    {"index": 1, "primary": false, "left": -1600, "top": -586, "width": 1000, "height": 1600, "image_base64": "<副屏 PNG>"}
  ],
  "screen_count": 2,
  "error": ""
}
```

- `screens` 按屏枚举，**主屏（`primary=true`）恒在第 0 位**，其余屏按屏幕左上角
  坐标排序；单屏时 `screen_count == 1`（仍有 1 项）。
- 每项 `image_base64` 为该屏独立截图；`left/top` 是该屏在虚拟屏中的坐标
  （副屏可为负），`width/height` 是该屏分辨率（物理像素）。
- 连接的是**旧版本地端**（无 screens 明细）时，hub 会把单图视作唯一主屏
  （`screen_count == 1`），你的多屏分支代码无需特判。

### 6.2 怎么写才算「兼容多屏」

**情况 A：你的插件没做多图识别（只想发一张图）——直接用 `image_base64`。**
它**恒为主屏截图**，这就是「优先发送主屏截图」的兜底语义，什么都不用改：

```python
resp = await self.ctx.api.call("cateye.connect-hub.screenshot", user_id=user_id)
if resp.get("success"):
    img_b64 = resp["image_base64"]   # 主屏；多屏时其余屏被有意忽略
```

**情况 B：你的插件能识别多张图片（如 send.hybrid 发多图、VLM 逐图描述）——遍历 `screens`。**

```python
resp = await self.ctx.api.call("cateye.connect-hub.screenshot", user_id=user_id)
if not resp.get("success"):
    ...  # 按 resp["error"] 处理
    return
for screen in resp.get("screens", []):
    b64 = screen.get("image_base64", "")
    if not b64:
        continue   # 负载超限被降级的屏没有图像（见 6.3），只有元数据
    label = "主屏" if screen.get("primary") else f"副屏{screen['index']}"
    await self.ctx.send.hybrid([{"type": "image", "content": b64}], stream_id)
```

> 判定口诀：**能发多图就读 `screens`（跳过空 `image_base64` 的项），
> 只发一张就读 `image_base64`（它永远是主屏）**。两个口径在单屏、多屏、
> 旧版本地端下行为一致，不需要自己判断屏数。

### 6.3 负载上限与自动降级

- 多屏 base64 总量可能很大（两块 1440p 屏不模糊时约 5-10 MB）。hub 的
  `connection.max_frame_mb`（默认 25，经鉴权应答通告给本地端）与本地端
  `screenshot.max_payload_mb`（默认 20）取更小者作为上限；
  **超限时本地端自动降级：只回传主屏图像**，其余屏保留元数据但 `image_base64`
  置空，降级说明写在 `data.notice`（经 `call` 返回值透传）。
- 你的插件若逐屏发送，遇到空 `image_base64` 直接跳过即可（不要当作错误）。

### 6.4 hub 自身的消费约定（供参照）

- `/sj` 命令：多屏时只发送主屏截图（聊天单图场景遵循主屏优先）。
- 「属于麦麦的生活」的屏幕感知（`companion/cateye_client.py`）：读
  `image_base64`，天然拿到主屏，无需改动。

## 7. 业务责任边界

- **留在你的插件**：QQ 命令注册、admin/权限校验、限流、长文本拆分、`send.*` 发送。
- **hub 负责**：连接、鉴权、请求-响应关联、超时与断连错误文案、事件分发、
  截图（/sj 命令与 `screenshot` API，模糊策略与限流都在 hub 配置里）。
