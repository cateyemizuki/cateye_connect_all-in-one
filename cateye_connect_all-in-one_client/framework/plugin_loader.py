"""扫描 plugins/ 目录并加载本地插件。

每个插件是一个子目录，包含：
  plugin.json  清单：{name, version, description, commands: [...]}
  plugin.py    入口：提供 create_plugin(ctx) 工厂，返回 LocalPlugin 实例

注意：插件目录会加入 sys.path（插件内部按顶层包导入自己的模块，
如 `from handlers.xxx import ...`）。多个插件请避免顶层模块重名。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from .log import Logger
from .plugin_base import LocalPlugin, PluginContext


class PluginLoadError(Exception):
    pass


def load_plugins(plugins_dir: Path, logger: Logger, emit_event) -> list[LocalPlugin]:
    """加载 plugins_dir 下的全部插件；单个插件失败不影响其他插件。"""
    plugins: list[LocalPlugin] = []
    seen_names: set[str] = set()
    seen_commands: dict[str, str] = {}

    if not plugins_dir.is_dir():
        logger.warn("插件目录不存在: %s", plugins_dir)
        return plugins

    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        manifest_path = entry / "plugin.json"
        entry_path = entry / "plugin.py"
        if not manifest_path.is_file() or not entry_path.is_file():
            continue  # 非插件目录静默跳过

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            name = str(manifest.get("name", "")).strip()
            version = str(manifest.get("version", "0.0.0"))
            description = str(manifest.get("description", ""))
            raw_commands = manifest.get("commands")
            if not isinstance(raw_commands, list) or not raw_commands:
                raise PluginLoadError("plugin.json 缺少 commands 或不是列表")
            commands = [str(c).strip() for c in raw_commands if isinstance(c, str) and str(c).strip()]
            if not name or not commands:
                raise PluginLoadError("plugin.json 缺少 name 或 commands")
            if "." in name or any(c.isspace() for c in name):
                raise PluginLoadError(f"插件名含非法字符（禁止 . 与空白）: {name}")
            if name in seen_names:
                raise PluginLoadError(f"插件名重复: {name}")
            dup = [c for c in commands if c in seen_commands]
            if dup:
                raise PluginLoadError(f"命令已被插件 {seen_commands[dup[0]]} 占用: {', '.join(dup)}")

            module = _import_module(entry_path, f"cateye_local_plugin_{name}", entry)

            data_dir = entry / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            ctx = PluginContext(
                name=name,
                plugin_dir=entry,
                data_dir=data_dir,
                logger=logger.child(name),
                emit_event=emit_event,
            )

            factory = getattr(module, "create_plugin", None)
            if not callable(factory):
                raise PluginLoadError("plugin.py 缺少 create_plugin(ctx) 工厂函数")
            plugin = factory(ctx)
            if not isinstance(plugin, LocalPlugin):
                raise PluginLoadError("create_plugin 未返回 LocalPlugin 实例")

            # 命令与版本以 plugin.json 清单为准
            plugin.commands = commands
            plugin.version = version
            plugin.description = description

            seen_names.add(name)
            for c in commands:
                seen_commands[c] = name
            plugins.append(plugin)
            logger.info("已加载插件 %s v%s（命令: %s）", name, version, ", ".join(commands))
        except Exception as e:
            logger.error("加载插件 %s 失败: %s", entry.name, e)

    return plugins


def _import_module(entry_path: Path, module_name: str, plugin_dir: Path):
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise PluginLoadError("无法创建导入规格")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
