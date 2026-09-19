#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冒烟测试：用 maibot_sdk stub 导入两个 MaiBot 端插件并实例化。

验证（不需要真实 MaiBot 环境）：
  1. 两个插件 plugin.py 可导入（相对导入、语法正确）
  2. create_plugin() 正常返回实例
  3. 装饰器（@Command / @API）正确挂载组件元数据，组件名无重复
  4. 配置模型可实例化

用法：python smoke_test_maibot_plugins.py
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def install_stub() -> None:
    """注入最小 maibot_sdk stub：装饰器透传、配置基类为普通类。"""
    stub = types.ModuleType("maibot_sdk")

    class _Component:
        def __init__(self, kind: str, name: str, **meta):
            self.kind = kind
            self.name = name
            self.meta = meta

        def __call__(self, fn):
            fn.__maibot_component_info__ = self
            return fn

    stub.Command = lambda name, **kw: _Component("Command", name, **kw)
    stub.API = lambda name, **kw: _Component("API", name, **kw)
    stub.Tool = lambda name, **kw: _Component("Tool", name, **kw)
    stub.HookHandler = lambda hook, **kw: _Component("HookHandler", hook, **kw)

    class Field:
        def __init__(self, default=None, **kw):
            self.default = default
            self.kw = kw

        def make(self):
            factory = self.kw.get("default_factory")
            return factory() if factory is not None else self.default

    class MaiBotPlugin:
        config_model = None
        ctx = None
        config = None

        async def on_load(self) -> None: ...
        async def on_unload(self) -> None: ...
        async def on_config_update(self, *a) -> None: ...
        def register_dynamic_api(self, *a, **kw) -> None: ...
        def clear_dynamic_apis(self) -> None: ...
        async def sync_dynamic_apis(self, *a, **kw) -> None: ...

    class PluginConfigBase:
        """假配置基类：支持 Field 默认值与 default_factory 嵌套节。"""

        def __init__(self, **kwargs):
            for name in dir(type(self)):
                val = getattr(type(self), name)
                if isinstance(val, Field):
                    setattr(self, name, kwargs.get(name, val.make()))

    stub.Field = Field
    stub.MaiBotPlugin = MaiBotPlugin
    stub.PluginConfigBase = PluginConfigBase
    sys.modules["maibot_sdk"] = stub


def collect_components(plugin_module) -> dict[str, list[str]]:
    """从插件实例类链上收集装饰器挂载的组件（kind → [name]）。"""
    inst = plugin_module.create_plugin()
    result: dict[str, list[str]] = {}
    seen: set[int] = set()
    for klass in type(inst).__mro__:
        for fn in vars(klass).values():
            if id(fn) in seen:
                continue
            seen.add(id(fn))
            info = getattr(fn, "__maibot_component_info__", None)
            if info is not None:
                result.setdefault(info.kind, []).append(info.name)
    return result


def find_plugin_class(plugin_module) -> type | None:
    for v in vars(plugin_module).values():
        if isinstance(v, type) and getattr(v, "config_model", None) is not None:
            return v
    return None


def main() -> None:
    install_stub()
    print("[1] 导入并实例化 MaiBot 端插件")
    modules = {}
    for dir_name, label in (
        ("cateye_connect_all-in-one_on_maibot", "cateye.connect-hub"),
        ("cateye_maibot-deepseek-harness-connect", "cateye.deepseek-harness-connect"),
    ):
        try:
            mod = importlib.import_module(f"{dir_name}.plugin")
            inst = mod.create_plugin()
            check(f"{label}: 导入 + create_plugin", inst is not None)
            modules[label] = mod
        except Exception as e:
            check(f"{label}: 导入 + create_plugin", False, repr(e))

    print("[2] 组件注册检查")
    expect = {
        "cateye.connect-hub": {
            "API": {"call", "submit", "screenshot", "status", "subscribe", "unsubscribe"},
            "Command": {"take_screenshot"},
        },
        "cateye.deepseek-harness-connect": {
            "Command": {"deepseek_chat", "cmd_run", "cmd_stop", "run_program", "run_preset"}
        },
    }
    for label, wants in expect.items():
        mod = modules.get(label)
        if mod is None:
            check(f"{label}: 组件", False, "未导入")
            continue
        got = collect_components(mod)
        for kind, names in wants.items():
            check(f"{label}: {kind} 组件齐全", set(got.get(kind, [])) == names,
                  f"实际: {sorted(got.get(kind, []))}")
        all_names = [n for ns in got.values() for n in ns]
        check(f"{label}: 组件名无重复", len(all_names) == len(set(all_names)))

    print("[3] 配置模型实例化")
    for label, mod in modules.items():
        plugin_cls = find_plugin_class(mod)
        try:
            cfg = plugin_cls.config_model()
            check(f"{label}: 配置模型默认实例化", cfg is not None)
        except Exception as e:
            check(f"{label}: 配置模型默认实例化", False, repr(e))

    print("[4] 生命周期容错（on_load 不得因空 token 等配置问题失败）")
    import asyncio

    class _PrintLogger:
        def info(self, msg, *a):
            print("      [log]", msg % a if a else msg)

        def warning(self, msg, *a):
            print("      [warn]", msg % a if a else msg)

        def error(self, msg, *a):
            print("      [err]", msg % a if a else msg)

    class _Ctx:
        logger = _PrintLogger()

    async def _lifecycle(mod) -> None:
        inst = mod.create_plugin()
        inst.ctx = _Ctx()
        inst.config = find_plugin_class(mod).config_model()
        await inst.on_load()
        await inst.on_unload()

    for label, mod in modules.items():
        try:
            asyncio.run(_lifecycle(mod))
            check(f"{label}: on_load/on_unload 正常完成", True)
        except Exception as e:
            check(f"{label}: on_load/on_unload 正常完成", False, repr(e))

    try:
        async def _empty_token() -> None:
            mod = modules["cateye.connect-hub"]
            inst = mod.create_plugin()
            inst.ctx = _Ctx()
            cfg = find_plugin_class(mod).config_model()
            assert not cfg.connection.ws_token, "默认配置应为空 token"
            inst.config = cfg
            await inst.on_load()
            assert inst._hub is not None and not inst._hub.connected, "服务不应启动"
            await inst.on_unload()

        asyncio.run(_empty_token())
        check("hub: 空 token 时加载成功且服务未启动（仅告警）", True)
    except Exception as e:
        check("hub: 空 token 时加载成功且服务未启动（仅告警）", False, repr(e))

    print()
    if FAILURES:
        print(f"冒烟测试失败: {FAILURES}")
        sys.exit(1)
    print("冒烟测试全部通过")


if __name__ == "__main__":
    main()
