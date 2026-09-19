# Cateye Connect 协议 v2

两端实现各持一份等价常量（MaiBot 端 `cateye_connect_all-in-one_on_maibot/protocol.py`、
本地端 `cateye_connect_all-in-one_client/framework/protocol.py`），帧结构以本文为准。

- 传输：WebSocket，JSON 文本帧，UTF-8，`ensure_ascii=False`
- 方向：MaiBot 端为**服务端**（被动监听），本地端为**客户端**（主动外连，自动重连）
- 模型：单客户端。新连接顶掉旧连接（旧连接被关闭）
- 心跳：客户端 `ping_interval=30, ping_timeout=10`；两端 `max_size=10MB`（截图 base64 用）

## 帧类型总览

| type | 方向 | 说明 |
|---|---|---|
| `auth` | 本地 → hub | 鉴权 + 插件注册表（连接后第一条消息） |
| `auth_ok` | hub → 本地 | 鉴权通过 |
| `auth_error` | hub → 本地 | 鉴权失败（随后断开） |
| `request` | hub → 本地 | 命令请求 |
| `response` | 本地 → hub | 命令响应（按 call_id 关联） |
| `event` | 本地 → hub | 主动事件（上行推送，hub 分发给订阅者） |

## 握手

1. 本地连接后 10s 内（`auth_timeout`）发送：

```json
{
  "type": "auth",
  "token": "<与 hub 配置 ws_token 一致>",
  "version": 2,
  "client": "cateye-connect-framework/1.0.0",
  "plugins": [
    {"name": "cateye_deepseek_harness", "version": "1.0.0",
     "description": "...", "commands": ["deepseek", "screenshot", "cmd_start", "cmd_stop", "run", "preset"]}
  ]
}
```

2. hub 校验成功 → `{"type": "auth_ok", "version": 2, "server": "cateye-connect-hub"}`；
   失败 → `{"type": "auth_error", "error": "鉴权失败：token 不正确"}` 并断开。
3. hub 记录插件注册表；此后 `request` 帧的 `command` 不在注册表内时，
   hub 直接返回错误（不占用本地往返）：`本地未加载可处理「<命令>」的插件`。

## 命令请求 / 响应

请求（hub → 本地）：

```json
{"type": "request", "call_id": "<uuid4>", "command": "deepseek",
 "args": {"prompt": "...", "workspace": "test"}, "user_id": "<QQ号，透传>"}
```

响应（本地 → hub）：

```json
{"type": "response", "call_id": "<同请求>", "result": "<文本；截图为 base64 PNG>", "error": ""}
```

- `error` 非空表示失败（`result` 应忽略）；两者均为字符串。
- hub 侧等待语义（错误文案与旧版一致）：
  - 未连接：`客户端未连接，请先启动本地客户端`
  - 超时：`本地处理超时，请重试`
  - 中途断开：`客户端连接失败，本地 PC 已断开连接`
- **兼容**：无 `type` 但带 `call_id`（+`command`）的帧按旧版 v1 语义处理
  （本地端视为 request，hub 端视为 response）。

## 命令清单（随插件注册，非协议固定）

| command | args | result |
|---|---|---|
| `deepseek` | `{prompt, workspace}` | Harness 最终答案文本 |
| `screenshot` | `{blur_radius}` | base64 PNG |
| `cmd_start` | `{window_id, command}` | 执行摘要 |
| `cmd_stop` | `{window_id}` 或 `{pid}` | 关闭结果 |
| `run` | `{program}` | 运行确认 |
| `preset` | `{name}` | 运行确认（未命中时 error 附可用列表） |

## 事件上行（预留扩展，已实现最小闭环）

本地插件 `await ctx.emit_event(event, data)` → 框架发送：

```json
{"type": "event", "event": "<事件名>", "data": {...}}
```

hub 端任何 MaiBot 插件经 `cateye.connect-hub.subscribe` 登记
`event → api_name` 后，hub 会 `ctx.api.call(api_name, event=..., data=...)` 通知订阅者。

## 扩展点

1. **新增本地命令**：本地插件在 `plugin.json` 的 `commands` 里登记即可，
   hub 握手后自动放行，MaiBot 侧插件直接 `call/submit` 该命令名。
2. **新增帧类型**：两端 `protocol.py` 各加常量；未知 type 的帧两端均只告警不崩溃，
   旧版本向前兼容。
3. **多客户端**：当前单客户端（新踢旧）；如需多本地机，把 hub 的 `_client`
   改为连接表并给 `request` 增加目标字段（协议已用 `type` 预留了演进空间）。
