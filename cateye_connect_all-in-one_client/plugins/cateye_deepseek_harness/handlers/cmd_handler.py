"""CMD 命令处理 — 持久 cmd 窗口，支持多行命令与窗口复用。

每个窗口 ID 对应一个独立的持久 cmd 进程（标题含窗口序号便于识别）。
多次向同一窗口发送命令会写入同一进程（继承之前的运行窗口）。
新窗口首次启动时先输出标记行，辅助确认窗口序号。
"""

import os
import subprocess

# 子进程环境：强制 Python 输出 GBK，匹配默认控制台代码页（避免子程序 UTF-8 输出乱码）
CHILD_ENV = dict(os.environ)
CHILD_ENV["PYTHONUTF8"] = "0"
CHILD_ENV["PYTHONIOENCODING"] = "gbk"


class CmdHandler:
    def __init__(self, config: dict, ctx):
        self.enabled = config.get("features", {}).get("cmd", True)
        cmd_cfg = config.get("cmd", {})
        # 工作目录相对插件 data 目录解析（<data>/cmds）
        self.data_dir = ctx.data_dir
        self.default_cwd = (ctx.data_dir / cmd_cfg.get("default_cwd", "./cmds")).resolve()
        self.default_cwd.mkdir(parents=True, exist_ok=True)
        # 窗口进程池: {window_id: Popen}
        self._windows: dict[int, subprocess.Popen] = {}

    def apply_config(self, config: dict) -> None:
        """WebUI 修改配置后就地应用（保留窗口进程池）。"""
        self.enabled = config.get("features", {}).get("cmd", True)
        cmd_cfg = config.get("cmd", {})
        new_cwd = (self.data_dir / cmd_cfg.get("default_cwd", "./cmds")).resolve()
        if new_cwd != self.default_cwd:
            self.default_cwd = new_cwd
        self.default_cwd.mkdir(parents=True, exist_ok=True)

    # ---- 内部工具 ----

    @staticmethod
    def _strip_outer_quotes(text: str) -> str:
        """去除外层匹配的引号，保留内部内容（含换行）。"""
        text = text.strip()
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            return text[1:-1]
        return text

    def _write(self, proc: subprocess.Popen, text: str) -> None:
        """向进程 stdin 写入一行命令（GBK 编码，匹配默认控制台代码页）。"""
        if proc.stdin:
            proc.stdin.write((text + "\r\n").encode("gbk", errors="replace"))
            proc.stdin.flush()

    def _ensure_window(self, win_id: int) -> subprocess.Popen:
        """获取或创建窗口进程（标题含序号，便于识别）。

        不使用 chcp（会与启动后立即写管道产生竞态导致窗口闪退）。
        改为 GBK 编码写入，天然匹配中文 Windows 默认代码页。
        """
        proc = self._windows.get(win_id)
        if proc and proc.poll() is None:
            return proc

        # /k 参数仅含 ASCII，避免中文编码歧义
        proc = subprocess.Popen(
            ["cmd", "/k", f"title MaiBot-CMD-Window-{win_id}"],
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
            cwd=str(self.default_cwd),
            env=CHILD_ENV,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        self._windows[win_id] = proc
        # 标记行与工作目录回显通过管道写入（GBK）
        self._write(proc, f"echo ###### MaiBot CMD 窗口 #{win_id} ######")
        self._write(proc, f"echo 工作目录: {self.default_cwd}")
        return proc

    # ---- 命令入口 ----

    async def handle_start(self, call_id: str, args: dict) -> dict:
        if not self.enabled:
            return {"call_id": call_id, "result": "", "error": "功能未启用: cmd"}

        try:
            win_id = int(args.get("window_id", 1))
        except (ValueError, TypeError):
            return {"call_id": call_id, "result": "", "error": "无效的窗口序号"}

        command = self._strip_outer_quotes(args.get("command", ""))
        if not command:
            return {"call_id": call_id, "result": "", "error": "命令为空"}

        try:
            proc = self._ensure_window(win_id)
        except Exception as e:
            return {"call_id": call_id, "result": "", "error": f"CMD 窗口启动失败: {e}"}

        # 多行支持：按行逐条写入同一窗口
        try:
            for line in command.splitlines():
                self._write(proc, line)
        except Exception as e:
            return {"call_id": call_id, "result": "", "error": f"命令写入失败: {e}"}

        lines = len(command.splitlines())
        summary = command.replace("\n", " ⏎ ")[:80]
        return {
            "call_id": call_id,
            "result": f"窗口 {win_id} 已执行{('（%d 行）' % lines) if lines > 1 else ''}: {summary}",
            "error": "",
        }

    @staticmethod
    def _decode_out(data: bytes) -> str:
        """安全解码子进程输出（taskkill 在中文 Windows 输出 GBK，避免乱码/崩溃）。"""
        if not data:
            return ""
        for enc in ("utf-8", "gbk"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _taskkill(pid: int):
        """强制终止指定 PID 的进程树，返回 CompletedProcess 供调用方判断成败。"""
        return subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
        )

    async def handle_stop(self, call_id: str, args: dict) -> dict:
        # /p|/pid 分支：/sdcmd|/sc /p|/pid <PID>，结束指定 PID 的进程树
        if args.get("pid") is not None:
            return self._stop_by_pid(call_id, args.get("pid"))

        try:
            win_id = int(args.get("window_id", 0))
        except (ValueError, TypeError):
            return {"call_id": call_id, "result": "", "error": "无效的窗口序号"}

        proc = self._windows.pop(win_id, None)
        if proc and proc.poll() is None:
            result = self._taskkill(proc.pid)
            if result.returncode == 0:
                return {"call_id": call_id, "result": f"窗口 {win_id} 已关闭", "error": ""}
            return {
                "call_id": call_id,
                "result": "",
                "error": f"关闭窗口 {win_id} 失败: {self._decode_out(result.stderr).strip()}",
            }
        return {"call_id": call_id, "result": f"窗口 {win_id} 不存在或已退出", "error": ""}

    def _stop_by_pid(self, call_id: str, pid) -> dict:
        """按 PID 关闭 cmd 窗口（含外部窗口）。"""
        try:
            pid = int(pid)
        except (ValueError, TypeError):
            return {"call_id": call_id, "result": "", "error": "无效的 PID"}

        # 若 PID 是我们管理的窗口，从进程池移除
        for win_id, proc in list(self._windows.items()):
            if proc.pid == pid:
                self._windows.pop(win_id, None)
                break

        result = self._taskkill(pid)
        if result.returncode == 0:
            return {"call_id": call_id, "result": f"已终止 PID {pid} 的进程树", "error": ""}
        return {
            "call_id": call_id,
            "result": "",
            "error": f"终止 PID {pid} 失败: {self._decode_out(result.stderr).strip()}",
        }
