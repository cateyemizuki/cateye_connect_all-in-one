"""/preset — 运行 data/preset 文件夹中预设的文件或脚本。"""

import os
import subprocess
from pathlib import Path


def _match_without_extension(name: str, target: str) -> bool:
    """匹配时排除文件最后一个 . 以及往后的内容。"""
    base = name.rsplit(".", 1)[0] if "." in name else name
    return base == target


class PresetHandler:
    def __init__(self, config: dict, ctx):
        self.enabled = config.get("features", {}).get("preset", True)
        preset_dir = config.get("work_dirs", {}).get("preset", "./preset")
        # 预设目录相对插件 data 目录解析（<data>/preset）；
        # 预设脚本内 `cd ../ADB` 依赖 data/preset 与 data/ADB 同级
        self.data_dir = ctx.data_dir
        self.preset_dir = (ctx.data_dir / preset_dir).resolve()
        self.preset_dir.mkdir(parents=True, exist_ok=True)

    def apply_config(self, config: dict) -> None:
        """WebUI 修改配置后就地应用（不重启框架）。"""
        self.enabled = config.get("features", {}).get("preset", True)
        preset_dir = config.get("work_dirs", {}).get("preset", "./preset")
        self.preset_dir = (self.data_dir / preset_dir).resolve()
        self.preset_dir.mkdir(parents=True, exist_ok=True)

    def handle(self, call_id: str, args: dict) -> dict:
        if not self.enabled:
            return {"call_id": call_id, "result": "", "error": "功能未启用: preset"}

        target = args.get("name", "").strip()
        if not target:
            return {"call_id": call_id, "result": "", "error": "请指定预设名称"}

        # 查找匹配文件（忽略后缀）
        matched: Path | None = None
        for f in self.preset_dir.iterdir():
            if f.is_file() and _match_without_extension(f.name, target):
                matched = f
                break

        if not matched:
            # 列出可用预设
            avail = [f.name for f in self.preset_dir.iterdir() if f.is_file()]
            listing = "、".join(avail) if avail else "（空）"
            return {"call_id": call_id, "result": "", "error": f"未找到预设「{target}」。可用预设: {listing}"}

        try:
            # 直接执行（不检查后缀，交给系统解释器）
            if os.name == "nt":
                proc = subprocess.Popen(
                    ["cmd", "/c", str(matched)],
                    cwd=str(matched.parent),
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                proc = subprocess.Popen([str(matched)], cwd=str(matched.parent))
            return {"call_id": call_id, "result": f"已运行预设: {matched.name} (PID {proc.pid})", "error": ""}
        except Exception as e:
            return {"call_id": call_id, "result": "", "error": f"运行预设失败: {e}"}
