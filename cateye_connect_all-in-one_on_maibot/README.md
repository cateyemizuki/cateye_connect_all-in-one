# cateye.connect-hub — MaiBot 统一连接插件

MaiBot 端与本地 PC 之间的**唯一** WebSocket 通道（服务端）。其他业务插件
一律通过它的公共 API 通信，不再各自管理连接。

## 公共 API（`ctx.api.call("cateye.connect-hub.<name>", ...)`）

| API | 说明 |
|---|---|
| `call(command, args, user_id, timeout)` | 同步发送命令并等待响应 → `{"result", "error"}` |
| `submit(command, args, user_id, reply_api, call_ref)` | 异步提交，立即返回 `{"success", "call_id", "error"}`；本地响应后回调 `reply_api`；连接断开时回调以 `error=断连文案` 触发（不静默丢弃） |
| `screenshot(user_id, blur=None)` | 获取本地 PC 截图 → `{"success", "image_base64", "error"}`（base64 PNG）。`blur` 缺省时按策略处理：`user_id` 是管理员则不模糊，否则按配置半径；显式传 `blur`（0-64）则覆盖策略。不做限流，调用方自律 |
| `status()` | 连接状态 + 本地插件注册表 |
| `subscribe(event, api_name)` / `unsubscribe` | 订阅本地端上行事件 |

接入方法见 [../docs/dev-maibot-plugin.md](../docs/dev-maibot-plugin.md)。

取图示例：

```python
resp = await self.ctx.api.call(
    "cateye.connect-hub.screenshot", user_id="123456",
)
if resp.get("success"):
    img_b64 = resp["image_base64"]   # 配合 ctx.send.hybrid 发送，或自行处理
```

## 命令

| 命令 | 说明 |
|---|---|
| `/视奸`（/sj、/screenshot） | 对本地 PC 截图并发送到当前聊天（管理员不模糊；非管理员按配置半径模糊 + 全局/单用户限流） |

## 配置（config.toml 由 Runner 生成）

| 节 | 字段 | 默认 | 说明 |
|---|---|---|---|
| `plugin` | `enabled` | true | 启用插件 |
| `connection` | `ws_host` | 0.0.0.0 | 监听地址 |
| | `ws_port` | 8765 | 监听端口 |
| | `ws_token` | （空） | 鉴权 Token，**必须设置** |
| | `auth_timeout` | 10 | 鉴权等待秒数 |
| | `max_frame_mb` | 10 | 单帧上限（截图 base64 用） |
| `screenshot` | `admin_users` | [] | 管理员 QQ 号（管理员截图不模糊；与业务插件的管理员列表保持一致） |
| | `blur_radius` | 5 | 非管理员截图模糊半径（0-64） |
| | `max_per_minute` | 10 | 全局截图限流（次/分钟，作用于 /sj 命令） |
| | `max_per_user_per_minute` | 2 | 单用户截图限流（次/分钟，作用于 /sj 命令） |
| | `command_timeout` | 60 | 等待本地截图响应秒数 |

## 行为要点

- 单客户端模型：新连接顶掉旧连接（与旧版一致）。
- 本地框架握手时上报插件/命令注册表；未知命令直接报错，不发往本地。
- 配置热更新（改端口/token）会自动重启 WS 服务。
- **ws_token 未设置时插件正常加载、WS 服务不启动**（仅有告警日志）；在配置中
  设置 token 并保存后服务自动启动，**无需重启 MaiBot**。端口被占用等启动失败
  同样只记错误日志，不会导致插件加载失败。
- 依赖本插件的其他插件（如 deepseek harness 连接插件）在服务未启动期间可正常
  加载与注册命令，调用 API 会得到「客户端未连接」类错误。
