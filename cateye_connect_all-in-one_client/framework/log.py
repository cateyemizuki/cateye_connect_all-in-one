"""极简日志：带级别前缀与可选模块前缀的控制台输出。"""

from __future__ import annotations

from datetime import datetime

_LEVEL_TAGS = {"info": "[i]", "warn": "[!]", "error": "[X]"}


class Logger:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix

    def child(self, prefix: str) -> "Logger":
        """带模块前缀的子 logger。"""
        return Logger(prefix)

    def _log(self, level: str, msg: str, *args) -> None:
        text = msg % args if args else msg
        ts = datetime.now().strftime("%H:%M:%S")
        name = f" [{self.prefix}]" if self.prefix else ""
        print(f"{ts} {_LEVEL_TAGS.get(level, '[i]')}{name} {text}", flush=True)

    def info(self, msg: str, *args) -> None:
        self._log("info", msg, *args)

    def warn(self, msg: str, *args) -> None:
        self._log("warn", msg, *args)

    def error(self, msg: str, *args) -> None:
        self._log("error", msg, *args)
