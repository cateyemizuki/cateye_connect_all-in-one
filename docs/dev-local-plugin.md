# 指南：开发本地端插件（cateye_connect_all-in-one_client）

本地框架负责 WS 连接、重连、鉴权与命令路由；你只需在
`cateye_connect_all-in-one_client/plugins/` 下新建一个目录实现业务。

## 1. 目录结构

```
plugins/<你的插件>/
├── plugin.json          必需：清单
├── plugin.py            必需：入口（create_plugin 工厂）
├── config.example.json  建议：配置模板（用户复制为 config.json）
├── config.json          可选：插件自己的配置（读法见下）
├── data/                框架自动创建：插件数据目录（勿放代码）
└── ...                  其他模块/资产
```

`plugin.json`：

```json
{
  "name": "my_plugin",
  "version": "1.0.0",
  "description": "做什么的",
  "commands": ["my_cmd", "my_other_cmd"]
}
```

- `name` 全局唯一；`commands` 里的每个命令名全局唯一（重复时插件加载失败，
  不影响其他插件）。
- `commands` 以 plugin.json 为准（框架会覆盖代码里的声明），握手时上报给 hub，
  MaiBot 侧插件即可调用这些命令名。

## 2. plugin.py

```python
from framework.plugin_base import LocalPlugin


class MyPlugin(LocalPlugin):
    name = "my_plugin"            # 与 plugin.json 一致
    version = "1.0.0"
    commands = ["my_cmd"]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.config = ctx.load_plugin_config({
            "some_default": True,       # config.json 缺失/缺字段时的默认值
        })

    async def on_load(self) -> None:
        self.ctx.logger.info("已加载")

    async def on_unload(self) -> None:
        pass

    async def handle_command(self, call_id: str, command: str, args: dict, user_id: str) -> dict:
        # 返回统一形态；框架负责包 call_id、异常兜底
        if command == "my_cmd":
            return {"result": f"echo: {args}", "error": ""}
        return {"result": "", "error": f"未知命令: {command}"}


def create_plugin(ctx) -> LocalPlugin:
    return MyPlugin(ctx)
```

### PluginContext（`ctx`）提供

| 成员 | 说明 |
|---|---|
| `ctx.name` | 插件名 |
| `ctx.plugin_dir` | 插件目录 Path |
| `ctx.data_dir` | 数据目录 Path（`<插件>/data`，已自动创建） |
| `ctx.logger` | 带插件名前缀的 logger（info/warn/error） |
| `ctx.load_plugin_config(defaults)` | 读 `<插件>/config.json`，合并 defaults |
| `await ctx.emit_event(event, data)` | 向 MaiBot 端推事件帧；未连接返回 False |

### 可选：WebUI 配置表单与热更新

1. **声明 config_schema**（plugin.json 可选字段，格式与框架 schema 相同），
   WebUI「插件配置」页即渲染中文表单；未声明时按当前配置值自动推断控件。
   字段格式：`{"key": "dsh.cwd", "type": "string|bool|int|list", "label": "...",
   "description": "...", "min": ..., "max": ..., "secret": false, "group 由 title 分组}`,
   顶层为分组数组：`[{"title": "DSH", "fields": [...]}]`。
2. **实现 on_config_update**（LocalPlugin 可选方法）：WebUI 保存插件配置后调用，
   收到合并后的完整配置；就地更新内存状态并 `return True`（立即生效），
   不实现则修改写入文件、重启框架后生效。

### 约定

- `handle_command` 返回 `{"result": str, "error": str}`；抛异常会被框架捕获并
  以 error 返回（连接不断开）。
- 长耗时工作直接在 `handle_command` 里 await（如 deepseek 的 headless 子进程），
  hub 侧配合 `submit` 模式即可不限时等待。
- 插件目录会被加入 `sys.path`，内部可按顶层包导入自己的模块
  （如 `from handlers.xxx import ...`）；多个插件之间避免顶层模块重名。
- 路径一律相对 `ctx.data_dir` / `ctx.plugin_dir` 解析，勿写死绝对路径。

## 3. 与 MaiBot 侧对接

- MaiBot 侧业务插件经统一连接插件调用你的命令名（`call`/`submit`），
  无需本地端做任何额外登记。
- 主动推送：`await self.ctx.emit_event("my_event", {"k": "v"})`；
  MaiBot 侧先 `subscribe`（见 docs/dev-maibot-plugin.md）。

## 4. 调试

不需要 MaiBot 即可联调（dev_tests/mock_hub.py 模拟 hub：鉴权 → 发未知命令 →
发 preset 列表命令并打印响应）：

```
cd cateye_connect_all-in-one_client
python ../dev_tests/mock_hub.py --port 18765 --token test
python main.py --ws ws://127.0.0.1:18765 --token test
```

真实环境：`config.json` 的 `ws_url`/`token` 指向 MaiBot 端统一连接插件即可。
