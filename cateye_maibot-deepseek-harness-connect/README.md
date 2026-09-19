# cateye.deepseek-harness-connect — MaiBot 业务插件

DeepSeek Harness 远程控制的 MaiBot 端命令插件。**不建立任何连接**：
全部通信经统一连接插件 `cateye.connect-hub` 完成（manifest 已声明 plugin 依赖）。
截图（/sj）由统一连接插件直接提供。

## 命令

| 命令 | 说明 | 通信方式 |
|---|---|---|
| `/deepseek <工作区> <内容>`（/dsh） | 本地 DeepSeek Harness 对话 | `hub.submit` 异步，无超时，结果经动态 API `on_result` 回调发送 |
| `/cmd <窗口> <命令>`（/c） | 持久 CMD 窗口 | `hub.call` |
| `/sdcmd <窗口>`（/sc，支持 `/p <PID>`） | 关闭窗口 / 结束进程树 | `hub.call` |
| `/run <程序>` | Windows 运行窗口 | `hub.call` |
| `/preset <名称>` | 预设脚本 | `hub.call` |

## 配置

| 节 | 字段 | 默认 | 说明 |
|---|---|---|---|
| `harness` | `admin_users` | [] | 管理员 QQ 号（/dsh、/cmd、/sdcmd、/run、/preset 均要求管理员） |
| | `command_timeout` | 60 | call 类命令超时秒数 |

## 依赖

- `cateye.connect-hub >=1.0.0`（连接与本地命令注册表由 hub 提供）
- 能力：`send.text` / `send.forward` / `api.call`
