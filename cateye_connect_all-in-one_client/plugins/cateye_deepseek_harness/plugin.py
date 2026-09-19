"""本地插件：DeepSeek Harness 对话 + 本地系统控制。

与 MaiBot 端 cateye.deepseek-harness-connect 插件的命令一一对应：
  deepseek    /dsh 长耗时大模型对话（headless 子进程，不设超时）
  screenshot  /sj 屏幕截图（PIL + 可选高斯模糊，返回 base64 PNG）
  cmd_start   /cmd 持久 CMD 窗口执行命令
  cmd_stop    /sdcmd 关闭 CMD 窗口或按 PID 结束进程树
  run         /run Windows 运行窗口
  preset      /preset 预设脚本（data/preset/，忽略后缀匹配）

配置：config.json（可选，缺省值见 DEFAULT_CONFIG，完整模板见 config.example.json）
数据：data/（deepseek 工作区、preset 预设、cmds 工作目录、ADB 脚本）
     preset 脚本内 `cd ../ADB` 的相对路径依赖 data/preset 与 data/ADB 同级，勿拆分。
"""

from __future__ import annotations

from typing import Any

from framework.plugin_base import LocalPlugin

from handlers.cmd_handler import CmdHandler
from handlers.deepseek_handler import DeepseekHandler
from handlers.preset_handler import PresetHandler
from handlers.run_handler import RunHandler
from handlers.screenshot_handler import ScreenshotHandler

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "dsh": {
        "command": ["node", "apps/cli/lib/bin.js", "--profile", "headless"],
        "cwd": "D:\\deepseek-harness",
        "workspace": "./deepseek",
        "timeout": 600,
    },
    "work_dirs": {
        "cmd": "./cmds",
        "preset": "./preset",
    },
    "features": {
        "deepseek": True,
        "screenshot": True,
        "cmd": True,
        "run": True,
        "preset": True,
    },
    "screenshot": {
        "blur_radius": 5,
    },
    "cmd": {
        "default_cwd": "./cmds",
    },
}


class CateyeDeepseekHarnessPlugin(LocalPlugin):
    name = "cateye_deepseek_harness"
    version = "1.0.0"
    description = "DeepSeek Harness 对话与本地系统控制"
    commands = ["deepseek", "screenshot", "cmd_start", "cmd_stop", "run", "preset"]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.config = ctx.load_plugin_config(DEFAULT_CONFIG)
        self.deepseek = DeepseekHandler(self.config, ctx)
        self.screenshot = ScreenshotHandler(self.config, ctx)
        self.cmd = CmdHandler(self.config, ctx)
        self.run = RunHandler(self.config, ctx)
        self.preset = PresetHandler(self.config, ctx)

    async def on_load(self) -> None:
        self.ctx.logger.info("DeepSeek Harness 本地插件已加载（数据目录: %s）", self.ctx.data_dir)

    async def on_config_update(self, config: dict) -> bool:
        """WebUI 修改配置后就地应用（保留 CMD 窗口进程池）。"""
        self.config = config
        self.deepseek.apply_config(config)
        self.screenshot.apply_config(config)
        self.cmd.apply_config(config)
        self.run.apply_config(config)
        self.preset.apply_config(config)
        self.ctx.logger.info("配置已热更新")
        return True

    async def handle_command(self, call_id: str, command: str, args: dict, user_id: str) -> dict:
        del user_id
        if command == "deepseek":
            return await self.deepseek.handle(call_id, args)
        if command == "screenshot":
            return self.screenshot.handle(call_id, args)
        if command == "cmd_start":
            return await self.cmd.handle_start(call_id, args)
        if command == "cmd_stop":
            return await self.cmd.handle_stop(call_id, args)
        if command == "run":
            return self.run.handle(call_id, args)
        if command == "preset":
            return self.preset.handle(call_id, args)
        return {"call_id": call_id, "result": "", "error": f"未知命令: {command}"}


def create_plugin(ctx) -> LocalPlugin:
    return CateyeDeepseekHarnessPlugin(ctx)
