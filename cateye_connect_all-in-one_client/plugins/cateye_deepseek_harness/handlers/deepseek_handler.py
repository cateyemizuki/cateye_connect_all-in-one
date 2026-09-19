"""DeepSeek Harness 对话命令处理（maibot 分组版）。

通过 DSH 的 headless 模式 + maibot-headless 插件（dsh-plugin/）执行任务：

  - 命令格式：/deepseek <工作区名称> <对话内容>（可缩写 /dsh）。
  - 每个工作区名称对应一个对话子文件夹：<data>/deepseek/<工作区名称>/，
    全部相对插件 data 目录解析（移动插件目录不失效），名称会做安全过滤。
  - harness 进程以 deepseek 根为工作目录启动；maibot 插件按工作区名称
    派生固定会话 id 并复用持久化会话（同一工作区 = 同一持续对话），
    会话标题 = 工作区名称，并确保名为 "maibot" 的 workspace 存在，
    因此 Web UI 中所有对话显示在 maibot 分组下。
  - 只取 stdout 中的最终答案（不含思维链）；stderr 仅在失败时使用。
"""

import asyncio
import re
import shlex
import shutil
from pathlib import Path

_NAME_RE = re.compile(r"^[\w-]+$")
# Windows 保留设备名不能作为目录名
_RESERVED_RE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


class DeepseekHandler:
    """处理 /deepseek 命令（别名 /dsh）。"""

    def __init__(self, config: dict, ctx):
        self.enabled = config.get("features", {}).get("deepseek", True)
        dsh_cfg = config.get("dsh", {})
        self.command = dsh_cfg.get("command", ["node", "apps/cli/lib/bin.js", "--profile", "headless"])
        self.cwd = dsh_cfg.get("cwd", "D:\\deepseek-harness")
        workspace = dsh_cfg.get("workspace", "./deepseek")
        # 工作区根目录：相对插件 data 目录解析（<data>/deepseek）
        self.data_dir = ctx.data_dir
        self.workspace_root = (ctx.data_dir / workspace).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.dsh_plugin_dir = ctx.plugin_dir / "dsh-plugin"
        self.patch_file = ctx.data_dir / ".maibot-headless.patch.yml"

    def apply_config(self, config: dict) -> None:
        """WebUI 修改配置后就地应用（不重启框架）。"""
        self.enabled = config.get("features", {}).get("deepseek", True)
        dsh_cfg = config.get("dsh", {})
        if dsh_cfg.get("command"):
            self.command = dsh_cfg["command"]
        if dsh_cfg.get("cwd"):
            self.cwd = dsh_cfg["cwd"]
        workspace = dsh_cfg.get("workspace", "./deepseek")
        self.workspace_root = (self.data_dir / workspace).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    # ---- 工具 ----

    @staticmethod
    def _to_args(command) -> list[str]:
        """兼容字符串（shlex 拆分）或列表两种写法。"""
        if isinstance(command, str):
            return shlex.split(command)
        return [str(c) for c in command]

    @staticmethod
    def _sanitize_workspace(name: str) -> str:
        """安全化工作区名称：仅保留 字母/数字/下划线/连字符/中文，其余替换为 _。

        禁止空名、"."、".." 及任何路径分隔符，防止路径穿越。
        """
        cleaned = re.sub(r"[^\w-]+", "_", name).strip("._")
        if not cleaned or not _NAME_RE.match(cleaned) or cleaned in (".", ".."):
            return ""
        if _RESERVED_RE.match(cleaned):
            return ""
        return cleaned[:64]

    def _workspace_dir(self, name: str) -> Path:
        """对话子文件夹：<deepseek 根>/<工作区名称>/（相对路径，自动创建）。"""
        safe = self._sanitize_workspace(name)
        if not safe:
            raise ValueError("工作区名称无效（仅允许字母、数字、下划线、连字符、中文）")
        ws_dir = self.workspace_root / safe
        ws_dir.mkdir(parents=True, exist_ok=True)
        return ws_dir

    def _ensure_patch(self) -> Path:
        """生成 headless 覆盖 patch（挂载 dsh-plugin/ 下的 maibot 插件）。

        patch 与插件路径在运行时按插件目录计算，移动插件目录后自动重新生成。
        """
        startup_uri = (self.dsh_plugin_dir / "maibot-startup.mjs").as_uri()
        runner_uri = (self.dsh_plugin_dir / "maibot-runner.mjs").as_uri()

        def q(value: str) -> str:
            # YAML 单引号标量转义：路径含撇号时不转义会截断值
            return value.replace("'", "''")

        content = (
            "# 由本地客户端自动生成，请勿手动编辑\n"
            "- id: headless-startup\n"
            "  disabled: true\n"
            "- id: headless-runner\n"
            "  disabled: true\n"
            "- insert:\n"
            # workspace 服务依赖链（与 web-app bundle 一致，共享 $DSH_HOME/storages）
            "  - id: storage\n    name: '@deepseek-ai/dsh-storage'\n"
            "  - id: storage-json\n    name: '@deepseek-ai/dsh-storage-json'\n"
            "    config:\n      root: !!js dshHomePath('storages')\n"
            "  - id: storage-domain\n    name: '@deepseek-ai/dsh-storage-domain'\n"
            "    config:\n      backend: json\n"
            "  - id: workspace\n    name: '@deepseek-ai/dsh-workspace'\n"
            f"  - id: maibot-startup\n    name: '{q(startup_uri)}'\n"
            f"  - id: maibot-runner\n    name: '{q(runner_uri)}'\n"
            "    inject: [maibotStartup]\n"
            "    config:\n"
            "      workspace: !!js ctx.maibotStartup.workspace\n"
            "      task: !!js ctx.maibotStartup.task\n"
        )
        self.patch_file.write_text(content, encoding="utf-8")
        return self.patch_file

    def _resolve_command(self, command: list[str]) -> tuple[list[str], str]:
        """解析启动命令与工作目录。

        命令中形如路径的参数（含 / 或 \\）相对 dsh.cwd 解析为绝对路径；
        默认命令使用的已构建 CLI 不存在时，回退为 pnpm 方式并从仓库根目录启动。
        """
        dsh_root = Path(self.cwd)
        resolved = []
        for arg in command:
            if ("/" in arg or "\\" in arg) and not Path(arg).is_absolute():
                resolved.append(str(dsh_root / arg))
            else:
                resolved.append(arg)

        # 回退：默认构建产物缺失 → pnpm 源码方式（必须在 dsh.cwd 中运行）
        bin_candidate = resolved[1] if len(resolved) > 1 else ""
        if bin_candidate.endswith((".js", ".mjs", ".cjs")) and not Path(bin_candidate).is_file():
            return ["pnpm", "dsh", "--profile", "headless"], str(dsh_root)

        return resolved, ""

    # ---- 命令入口 ----

    async def handle(self, call_id: str, args: dict) -> dict:
        if not self.enabled:
            return {"call_id": call_id, "result": "", "error": "功能未启用: deepseek"}

        prompt = (args.get("prompt") or "").strip()
        workspace = (args.get("workspace") or "").strip()
        if not prompt or not workspace:
            return {"call_id": call_id, "result": "", "error": "用法：/deepseek <工作区名称> <对话内容>"}

        try:
            self._workspace_dir(workspace)
        except ValueError as e:
            return {"call_id": call_id, "result": "", "error": str(e)}
        safe_name = self._sanitize_workspace(workspace)

        # 组装命令：node <bin> --profile headless --patch <patch> --maibot-workspace <名称> <prompt>
        cmd = self._to_args(self.command)
        cmd += ["--patch", str(self._ensure_patch()), "--maibot-workspace", safe_name]
        resolved_cmd, fallback_cwd = self._resolve_command(cmd)
        # prompt 最后追加：不参与路径解析（可能包含斜杠）
        resolved_cmd = resolved_cmd + [prompt]
        # 工作目录：默认 deepseek 根（统一会话分组）；回退 pnpm 方式时使用仓库根目录
        run_cwd = fallback_cwd or str(self.workspace_root)
        exe = shutil.which(resolved_cmd[0]) or resolved_cmd[0]

        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                *resolved_cmd[1:],
                cwd=run_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0),
            )
            # 不对 Harness 进程设超时：大模型复杂工作可能远超固定时限，
            # 由 MaiBot 端 /dsh（hub.submit 模式）无超时等待完成
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            return {
                "call_id": call_id,
                "result": "",
                "error": f"未找到命令 {resolved_cmd[0]}，请检查插件 config.json 的 dsh 配置",
            }
        except Exception as e:
            return {"call_id": call_id, "result": "", "error": f"DeepSeek Harness 启动失败: {e}"}

        text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            tail = (err_text or text)[-500:]
            return {
                "call_id": call_id,
                "result": "",
                "error": f"DeepSeek Harness 执行失败（exit {proc.returncode}）: {tail}",
            }
        if not text:
            return {"call_id": call_id, "result": "", "error": "DeepSeek Harness 无输出内容"}
        return {"call_id": call_id, "result": text, "error": ""}
