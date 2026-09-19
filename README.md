# Cateye Connect All-in-One

MaiBot 与本地 PC 之间的**统一 WebSocket 连接**架构。由旧版一对插件
（`maibot-deepseek-harness-connect` + `maibot-deepseek-harness-client`）拆分而来，
连接职责与业务职责分离，两端均预留插件接入 API。

## 架构

```
QQ ⇄ MaiBot
 ├─ ① cateye.connect-hub（MaiBot 统一连接插件）
 │    · 唯一持有 WS 服务端（默认 0.0.0.0:8765，token 鉴权，单客户端）
 │    · 对其他 MaiBot 插件暴露公共 API：call / submit / screenshot / status / subscribe / unsubscribe
 │    · 截图命令 /sj（/screenshot）由本插件直接提供（模糊策略与限流集中在此）
 │    ⬑ ② cateye.deepseek-harness-connect（MaiBot 业务插件）
 │         · 注册 /dsh /cmd /sdcmd /run /preset 命令，admin 校验
 │         · 全部经 ctx.api.call 调 ① 与本地通信，自己不建连接
 │
 └─ WS（协议 v2，见 docs/protocol-v2.md）
      ⬑ ③ cateye_connect_all-in-one_client（本地框架）
           · WS 客户端：重连、TLS、鉴权（上报插件注册表）、命令路由、事件上行
           · 插件加载器：扫描 plugins/*/（plugin.json + create_plugin）
           · 自带 WebUI（默认 http://127.0.0.1:8766）：编辑框架与各插件的配置文件
           ⬑ ④ plugins/cateye_deepseek_harness（本地业务插件，自包含）
                · deepseek / screenshot / cmd_start / cmd_stop / run / preset
                · 自带 handlers、dsh-plugin、data/{deepseek,preset,cmds,ADB}
```

| 组件 | 目录 | 运行位置 | 职责 |
|---|---|---|---|
| ① 统一连接插件 | `cateye_connect_all-in-one_on_maibot/` | MaiBot 插件目录 | 连接通道 + 对内 API + /sj 截图命令 |
| ② 业务插件 | `cateye_maibot-deepseek-harness-connect/` | MaiBot 插件目录 | QQ 命令 → hub API |
| ③ 本地框架 | `cateye_connect_all-in-one_client/` | 本地 PC | 连接 + 插件加载 + 路由 |
| ④ 本地插件 | `cateye_connect_all-in-one_client/plugins/cateye_deepseek_harness/` | ③ 的 plugins/ | 实际业务处理 |

## 快速开始

### MaiBot 端

1. 把 ①② 两个目录复制到 MaiBot 的插件目录（旧插件 `maibot-community.deepseek-harness-connect`
   需先停用/移除，避免 8765 端口冲突）。
2. 在 ① 的配置（WebUI 或 config.toml）设置 `connection.ws_token`：
   未设置时插件正常加载但 WS 服务不启动（仅告警），设置后保存配置即自动启动。
3. 在 ② 的配置设置 `harness.admin_users`（管理员 QQ 号）。
4. 重启 MaiBot。② 依赖 ①（manifest 已声明 plugin 依赖），会自动保证加载顺序。

### 本地端

1. 进入 `cateye_connect_all-in-one_client/`，运行 `启动客户端.cmd`
   （首次运行自动建 venv、装依赖、从 `config.example.json` 生成 `config.json`，
   并自动生成 WebUI 访问密码）。
2. 浏览器打开 WebUI `http://127.0.0.1:8766`（密码见 `config.json` 的
   `webui.password` 或控制台日志），在「框架配置」里填 `ws_url`/`token`，
   保存后自动重连。
3. 本地插件 ④ 已随框架自动加载（握手时上报命令注册表，hub 据此校验命令）。

> 旧的 deepseek 工作区数据已复制到 ④ 的 `data/deepseek/`；如需最新数据，
> 从旧客户端 `deepseek/` 再复制覆盖即可（排除 `__pycache__`）。

## 命令（与旧版完全一致）

| 命令 | 说明 | 超时 |
|---|---|---|
| `/deepseek <工作区> <内容>`（/dsh） | 调用本地 DeepSeek Harness 对话 | 无（submit 异步回调） |
| `/视奸`（/sj、/screenshot） | 本地截图（非管理员高斯模糊） | command_timeout |

> 截图命令由 ① 统一连接插件提供；其余命令由 ② DeepSeek Harness 连接插件提供。
> 其他插件可经 `cateye.connect-hub.screenshot` API 直接获取截图（见下节）。
| `/cmd <窗口> <命令>`（/c） | 持久 CMD 窗口执行命令 | command_timeout |
| `/sdcmd <窗口>`（/sc，支持 `/p <PID>`） | 关闭 CMD 窗口 / 结束进程树 | command_timeout |
| `/run <程序>` | Windows 运行窗口 | command_timeout |
| `/preset <名称>` | 运行预设脚本 | command_timeout |

## 预留 API（接入更多插件）

- **MaiBot 侧**：任何插件依赖 `cateye.connect-hub` 后，即可
  `await self.ctx.api.call("cateye.connect-hub.call", command=..., args=...)`
  与本地端任意已注册命令通信；需要截图时直接调
  `cateye.connect-hub.screenshot`（返回 base64 PNG，无需自建连接）；
  详见 [docs/dev-maibot-plugin.md](docs/dev-maibot-plugin.md)。
- **本地侧**：在 `cateye_connect_all-in-one_client/plugins/` 下按规范新建目录即可被
  框架自动加载；详见 [docs/dev-local-plugin.md](docs/dev-local-plugin.md)。
- **协议**：帧格式与扩展点见 [docs/protocol-v2.md](docs/protocol-v2.md)。

## 联调工具

`dev_tests/mock_hub.py`：不启动 MaiBot 即可测试本地框架与插件
（模拟 hub 的鉴权、未知命令、preset 列表）：

```
cd cateye_connect_all-in-one_client
python ../dev_tests/mock_hub.py --port 18765 --token test
# 另开终端：
python main.py --ws ws://127.0.0.1:18765 --token test
```

## 目录速览

```
cateye_connect_all-in-one/
├── LICENSE                                     MIT 许可证
├── README.md                                   本文件
├── docs/                                       协议与开发指南
├── dev_tests/                                  mock hub 与冒烟测试
├── cateye_connect_all-in-one_on_maibot/        ① 统一连接插件
├── cateye_maibot-deepseek-harness-connect/     ② 业务插件
└── cateye_connect_all-in-one_client/           ③ 本地框架（含 ④ plugins/）
```

## 许可证

本项目（含 ①②③④ 四个组件）以 [MIT](LICENSE) 协议发布。
