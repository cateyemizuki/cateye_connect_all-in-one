# Cateye Connect 本地端框架

连接统一连接插件（MaiBot 端 `cateye.connect-hub`），把远端命令分发给
`plugins/` 下的本地插件处理。连接、重连、鉴权、路由、事件上行都在框架里；
**业务逻辑全部在插件里**。

## 使用

1. 运行 `启动客户端.cmd`（首次自动建 venv、装依赖、从模板生成 `config.json`）。
2. 编辑 `config.json`：`ws_url`（如 `wss://服务器IP:8765`）、`token`（与 hub 的
   `ws_token` 一致）；再运行启动脚本。

| 字段 | 默认 | 说明 |
|---|---|---|
| `ws_url` | ws://127.0.0.1:8765 | 服务端地址；`wss://` 前缀启用 TLS |
| `token` | （空） | 鉴权 Token，必填 |
| `ws_tls` | — | `{insecure, ca_file}`，自签名/IP 证书场景 |
| `reconnect_interval` | 5 | 断线重连间隔秒 |
| `auth_timeout` | 10 | 鉴权等待秒 |
| `plugins_dir` | ./plugins | 插件目录 |
| `webui` | 见下 | 配置编辑界面（enabled/host/port/password） |

也可命令行临时覆盖：`python main.py --ws ws://127.0.0.1:8765 --token xxx`。

## 开机自启动

三种开启方式（等价，任选其一）：

1. 双击框架根目录的 `开机自启-开启.cmd`（取消运行 `开机自启-关闭.cmd`）；
2. WebUI 状态页的「开机自启动」按钮；
3. 命令行 `python main.py --autostart on`（off 关闭 / status 查看状态）。

原理：在当前用户的启动文件夹
（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`）创建指向
`启动客户端.cmd` 的快捷方式，登录 Windows 时以最小化窗口自动启动框架。
仅影响当前用户，无需管理员权限。

## WebUI（配置编辑界面）

框架自带 WebUI（参考 MaiBot WebUI 的交互模式），默认随框架启动：
`http://127.0.0.1:8766`。首次启动自动生成访问密码并写回 `config.json`
的 `webui.password`（控制台日志同时打印）；修改密码后需重新登录；
监听非回环地址时必须设置密码。

- **状态页**：连接状态、已加载插件及其命令、手动重连。
- **框架配置页**：编辑 `config.json`（连接参数/插件目录/WebUI 自身）。
  修改连接参数保存后自动断开重连生效；标有「需重启」的项保存后提示重启。
- **插件配置页**：编辑各插件目录下的 `config.json`。声明了 `config_schema`
  的插件渲染中文表单，未声明的按当前值自动推断；保存后调用插件的
  `on_config_update` 热更新（插件实现该钩子即可立即生效，见 docs/dev-local-plugin.md）。
- 两种编辑模式：表单 / 原始 JSON；保存前自动把原文件备份为 `.bak`，
  未在表单中的自定义字段保留。

## 业务插件

框架**不内置业务插件**：把自建插件目录放到 `plugins/<插件名>/`（含 `plugin.json`
与 `create_plugin`）即被自动加载，写法见 [../docs/dev-local-plugin.md](../docs/dev-local-plugin.md)。
插件私有数据放各自的 `<插件>/data/`（`ctx.data_dir` 已自动创建）。

## 从旧版单文件客户端迁移

旧 `local_config.json` 的字段按职责拆分：`ws_url`/`token`/`ws_tls`/
`reconnect_interval` 属于本框架的 `config.json`（client 根目录）；业务字段
（对话 / 工作目录 / 功能开关 / 截图等）属于各业务插件自己的
`plugins/<插件名>/config.json`。旧版从未生效的字段（`dsh.timeout`、
`work_dirs.cmd`、`shell_whitelist` 及本地限流项——限流实际由 MaiBot 侧执行）已移除。

## 开发新插件

见 [../docs/dev-local-plugin.md](../docs/dev-local-plugin.md)；无 MaiBot 环境时
可用 `../dev_tests/mock_hub.py` 联调。

## 结构

```
main.py                入口（组装：加载插件 → 路由 → 连接 → WebUI；--autostart 管理自启动）
framework/
  autostart.py         开机自启动（用户启动文件夹快捷方式）
  config.py            config.json 加载/保存/备份/密码生成
  log.py               控制台日志
  protocol.py          协议 v2 常量（与 hub 端等价）
  schema.py            配置 schema 格式与类型矫正/合并工具（WebUI 用）
  plugin_base.py       LocalPlugin 基类 + PluginContext（含 on_config_update 钩子）
  plugin_loader.py     plugins/ 扫描与加载（plugin.json + create_plugin）
  router.py            command → 插件路由
  ws_client.py         WS 客户端（重连/鉴权/分发/事件上行/断开重连）
  webui/               配置编辑 WebUI（server.py + static/index.html）
```
