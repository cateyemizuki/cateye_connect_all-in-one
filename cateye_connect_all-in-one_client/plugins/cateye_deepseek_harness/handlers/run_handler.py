"""/run — 调用 Windows 运行窗口。"""

import subprocess


class RunHandler:
    def __init__(self, config: dict, ctx):
        del ctx
        self.enabled = config.get("features", {}).get("run", True)

    def apply_config(self, config: dict) -> None:
        """WebUI 修改配置后就地应用（不重启框架）。"""
        self.enabled = config.get("features", {}).get("run", True)

    def handle(self, call_id: str, args: dict) -> dict:
        if not self.enabled:
            return {"call_id": call_id, "result": "", "error": "功能未启用: run"}
        program = args.get("program", "").strip()
        if not program:
            return {"call_id": call_id, "result": "", "error": "请指定要运行的程序"}
        try:
            subprocess.Popen(["cmd", "/c", "start", program], shell=True)
            return {"call_id": call_id, "result": f"已运行: {program}", "error": ""}
        except Exception as e:
            return {"call_id": call_id, "result": "", "error": f"运行失败: {e}"}
