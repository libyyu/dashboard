# -*- coding: utf-8 -*-
"""CodeBuddyCliProvider：直接 subprocess 调用 CodeBuddy CLI，把 stream-json
映射成统一 AgentEvent。

与 CBOpenAIProvider（cb sidecar 的 /v1/agent/events HTTP 面）、OpenAICompatProvider
（标准 OpenAI /v1/chat/completions）并列，是第三种接入形态：不做 HTTP 客户端，而是
进程管理方。参考 plateau-atlas 的 codebuddy_cli.py：

- node 运行时来自共享运行时目录 runtimes/node/{version}/（codebuddy-cli 插件安装时
  自动下载，不再探测宿主 WorkBuddy/CodeBuddy IDE）
- CLI 入口来自插件目录 plugins/codebuddy-cli/{version}/cli/（ESM 脚本优先）
- 拼命令：--print --output-format stream-json --permission-mode dontAsk
  --strict-mcp-config --mcp-config {} --no-session-persistence --max-turns 6
- 对话历史渲染成文本 transcript 经 stdin 传入（单次无状态问答，Agent 工具循环留待后续）；
  带图片附件时改走 --input-format stream-json，图片以 image block 随 transcript 传入
- 逐行解析 stream-json（system/init、assistant(text/thinking/tool_use)、
  user(tool_result)、result、error）→ AgentEvent

取消协议与另两个 provider 一致：cancel_event 置位即停止；并把 subprocess 句柄暴露到
upstream_holder["process"]，由 cancel 端点 terminate 掉，让阻塞中的 readline 立即返回。

依赖仅 stdlib（subprocess），契合后端零第三方依赖 + PyInstaller 冻结约束。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from app.core.agent import AgentEvent
from app.runtime import ports
from app.adapters.storage_factory import get_storage

_STREAM_TIMEOUT = 1800.0
# 供 cancel 端点识别「进程句柄」的 key（与 cb_openai_provider 的 "response" 并列）
_PROCESS_KEY = "process"

_WINDOWS = os.name == "nt"
if _WINDOWS:
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
else:
    _CREATE_NO_WINDOW = 0

# CLI 不可用时兜底返回的内置模型（对齐公开版 codebuddy 的 --model 支持清单）
CLI_MODELS = ("deepseek-v4.1-flash", "deepseek-v4-pro-ioa")

def _version_key(name: str) -> tuple[int, ...]:
    """把 '22.22.2' 转成可比较的整数元组，用于运行时/插件目录的版本排序。"""
    parts: list[int] = []
    for chunk in str(name).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def _profiles_root() -> Path:
    return ports.home_dir() / "profiles" / ports.profile_id()


def _plugin_installed() -> bool:
    """codebuddy-cli 插件是否已安装 —— 这是整个 CLI 能力的总开关。

    即使系统全局装了 node / @tencent-ai/codebuddy-code，只要插件没装，
    provider 一律不暴露（available() False），相关服务全部禁用。
    """
    plugin_root = _profiles_root() / "plugins" / "codebuddy-cli"
    if not plugin_root.is_dir():
        return False
    return any(child.is_dir() for child in plugin_root.iterdir())


# CLI 插件包内相对插件版本目录的入口路径（ESM 优先，bin/codebuddy shim 旧版兜底）。
_CLI_RELATIVE_ENTRIES = (
    ("cli", "dist", "codebuddy-lite-wb.mjs"),
    ("cli", "dist", "codebuddy-headless.mjs"),
    ("cli", "dist", "codebuddy.mjs"),
    ("cli", "bin", "codebuddy"),
    ("cli", "dist", "codebuddy.js"),
)


def _find_cli_entry() -> str:
    """只在插件目录 plugins/codebuddy-cli/{version}/ 下找 CLI 入口，不探测宿主。"""
    plugin_root = _profiles_root() / "plugins" / "codebuddy-cli"
    if not plugin_root.is_dir():
        return ""
    candidates = sorted(
        (d for d in plugin_root.glob("*") if d.is_dir()),
        key=lambda d: _version_key(d.name),
        reverse=True,
    )
    for version_dir in candidates:
        for parts in _CLI_RELATIVE_ENTRIES:
            candidate = version_dir.joinpath(*parts)
            if candidate.is_file():
                return str(candidate)
    return ""


def _node_bin_name() -> str:
    """当前平台的 node 可执行文件名（Windows node.exe，其余 node）。"""
    return "node.exe" if os.name == "nt" else "node"


def _find_node_exe() -> str:
    """只在共享运行时 runtimes/node/{version}/ 下找 node 入口，不探测宿主。

    只看入口文件是否存在，不再依赖 .complete 安装标记；能否真正运行由
    cli_env_check 里的 `node --version` 实测判定。
    """
    node_root = _profiles_root() / "runtimes" / "node"
    if not node_root.is_dir():
        return ""
    bin_name = _node_bin_name()
    candidates = sorted(
        (p for p in node_root.glob(f"*/{bin_name}") if p.is_file()),
        key=lambda p: _version_key(p.parent.name),
        reverse=True,
    )
    return str(candidates[0]) if candidates else ""


def _node_version_ok(node: str) -> tuple[bool, str]:
    """实际运行 `node --version` 验证 node 可执行，返回 (是否成功, 版本或错误)。"""
    try:
        proc = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        return False, (proc.stderr.strip() or f"退出码 {proc.returncode}")
    except Exception as exc:  # noqa: BLE001 - 启动失败即视为不可用
        return False, str(exc)


def _find_global_codebuddy() -> tuple[str, str] | None:
    """探测全局安装的公开 CodeBuddy CLI（npm 全局装 @tencent-ai/codebuddy-code）。

    返回 (node, script)，找不到返回 None。公开版自带 cli-external-link 浏览器登录，
    登录态存 ~/.codebuddy/，脱离 IDE 也能无感运行；优先于插件内嵌的 WorkBuddy 桌面版。
    """
    exe = shutil.which("codebuddy")
    if not exe:
        return None
    cmd_dir = Path(exe).resolve().parent
    script = cmd_dir / "node_modules" / "@tencent-ai" / "codebuddy-code" / "bin" / "codebuddy"
    if not script.is_file():
        return None
    node = cmd_dir / "node.exe"
    node_path = str(node) if node.is_file() else "node"
    return node_path, str(script)


def _resolve_cli() -> tuple[str, str] | None:
    """解析可用的 (node, cli)。插件未安装一律返回 None（能力开关）。

    优先级：全局公开 CLI（自带 ~/.codebuddy 登录态）→ 插件内嵌 WorkBuddy 版。
    （插件本地 npm 安装的 codebuddy-code 由后续「检查/修复」功能补上。）
    """
    if not _plugin_installed():
        return None
    global_cli = _find_global_codebuddy()
    if global_cli:
        return global_cli
    cli = _find_cli_entry()
    node = _find_node_exe()
    if cli and node:
        return node, cli
    return None


def _render_prompt(messages: list[dict[str, object]]) -> str:
    """把 OpenAI 格式的消息序列渲染成一段带角色标签的文本 transcript。"""
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = _content_text(message.get("content"))
        if not content.strip():
            continue
        if role == "system":
            parts.append(f"<system>\n{content.strip()}\n</system>")
        elif role == "assistant":
            parts.append(f"<assistant>\n{content.strip()}\n</assistant>")
        elif role == "tool":
            parts.append(f"<tool>\n{content.strip()}\n</tool>")
        else:  # user 及未知角色一律按 user
            parts.append(f"<user>\n{content.strip()}\n</user>")
    return "\n\n".join(parts)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
        return "\n".join(texts)
    return ""


def _image_blocks(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """收集消息里的图片（OpenAI image_url dataUrl）→ CLI stream-json 的 image block。

    只支持 dataUrl（前端图片附件产生的就是它）；http(s) URL 无法确定媒体类型，跳过。
    """
    blocks: list[dict[str, object]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else ""
            url = str(url or "")
            if not url.startswith("data:"):
                continue
            head, _, data = url.partition(",")
            mime = head[5:].split(";", 1)[0] or "image/png"
            if not data:
                continue
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
    return blocks


def _block_type(block: object) -> str:
    return str(block.get("type") or "") if isinstance(block, dict) else ""


def _tool_result_text(content: object) -> str:
    """工具结果的 content 可能是字符串或 blocks 数组，归一为文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "\n".join(texts)
    return ""


# CLI 子进程需要保留的 CODEBUDDY_* 白名单（登录票据目录 / API key / 网络环境）。
# 其余 CODEBUDDY_*（SESSION_ID / TOOL_CALL_ID / EH_ROLE / EH_AFFINITY / NODE_BIN /
# API_KEY_BAK / SAFE_DELETE_* / MCP_CONFIG / SERVICE_PROXY_URL …）是宿主 IDE 注入的
# 会话变量，CLI 继承后误连 event hub / 误用会话 / 误用 node 会挂起，必须剔除。
_KEEP_ENV = ("CODEBUDDY_CONFIG_DIR", "CODEBUDDY_API_KEY", "CODEBUDDY_INTERNET_ENVIRONMENT")


def _build_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("CODEBUDDY_"):
            if key in _KEEP_ENV:
                env[key] = value
            continue
        env[key] = value
    # 避免 Git Bash 检测卡死（reg.exe 被安全软件拦截）、禁用 Remote Control 与内置插件市场
    env["CODEBUDDY_SKIP_GIT_BASH_CHECK"] = "1"
    env["CODEBUDDY_REMOTE_CONTROL_DISABLED"] = "1"
    env["CODEBUDDY_DISABLE_REMOTE_CONTROL"] = "1"
    env["CODEBUDDY_SKIP_BUILTIN_MARKETPLACE"] = "1"
    api_key = load_api_key()
    if api_key:
        env["CODEBUDDY_API_KEY"] = api_key
    return env


# --------------------------------------------------------------------------- #
# API key 持久化（登录后保存，后续会话复用）
# --------------------------------------------------------------------------- #
_CLI_AUTH_COLLECTION = "codebuddy_cli_auth"
_CLI_AUTH_KEY = "auth"


def load_api_key() -> str:
    storage = get_storage()
    if storage is None:
        return ""
    record = storage.get(_CLI_AUTH_COLLECTION, _CLI_AUTH_KEY)
    if isinstance(record, dict):
        return str(record.get("api_key") or "").strip()
    return ""


def save_api_key(key: str) -> None:
    storage = get_storage()
    if storage is not None:
        storage.put(_CLI_AUTH_COLLECTION, _CLI_AUTH_KEY, {"api_key": key.strip()})


def has_usable_auth() -> bool:
    """粗略判断是否已有可用登录：插件未装一律 False；否则看是否存过 API key 或全局公开 CLI 就位。"""
    if not _plugin_installed():
        return False
    return bool(load_api_key()) or _find_global_codebuddy() is not None


def _latest_version_name(root: Path) -> str:
    """取目录下最高版本子目录名（按 _version_key 排序），无则空串。"""
    if not root.is_dir():
        return ""
    dirs = sorted(
        (d for d in root.glob("*") if d.is_dir()),
        key=lambda d: _version_key(d.name),
        reverse=True,
    )
    return dirs[0].name if dirs else ""


def cli_env_check() -> dict[str, object]:
    """codebuddy-cli 运行环境检查：逐项探测插件 / node / cli / 登录态。

    供插件商店「自动检查 + 修复」使用；available 与 CodeBuddyCliProvider.available()
    同源（插件已装 且 (全局 CLI 或 node+cli 就位)），前端据此显示状态与修复按钮。
    """
    plugin_root = _profiles_root() / "plugins" / "codebuddy-cli"
    plugin_installed = _plugin_installed()
    node = _find_node_exe()
    cli = _find_cli_entry()
    global_cli = _find_global_codebuddy()
    auth_ready = has_usable_auth()

    plugin_version = _latest_version_name(plugin_root) if plugin_installed else ""

    # node 可用性 = 入口文件存在 + 实际跑 `node --version` 成功；文件在但跑不起来也判不可用。
    node_ok = False
    node_detail = "未安装（runtimes/node）"
    if node:
        ok, out = _node_version_ok(node)
        node_ok = ok
        node_detail = f"v{out}" if ok else f"不可运行：{out}"

    checks: list[dict[str, object]] = [
        {
            "id": "plugin", "label": "codebuddy-cli 插件", "ok": plugin_installed,
            "detail": f"已安装 v{plugin_version}" if plugin_installed else "未安装",
        },
        {
            "id": "node", "label": "共享 node 运行时", "ok": node_ok,
            "detail": node_detail,
        },
        {
            "id": "cli", "label": "CLI 入口", "ok": bool(cli),
            "detail": cli or "未找到 CLI 入口（cli/dist/*.mjs）",
        },
        {
            "id": "auth", "label": "登录态", "ok": auth_ready,
            "detail": "已登录（API key 或全局 CLI）" if auth_ready else "未登录（运行前需登录）",
        },
    ]

    # 与 _resolve_cli 同源：插件是总开关，未装插件即使有全局 CLI 也不可用。
    available = plugin_installed and (global_cli is not None or (node_ok and bool(cli)))
    if not plugin_installed:
        action = "install"
    elif not available:
        action = "repair"
    else:
        action = "none"

    return {
        "available": available,
        "action": action,
        "plugin_installed": plugin_installed,
        "node_ready": node_ok,
        "cli_ready": bool(cli),
        "global_cli": global_cli is not None,
        "auth_ready": auth_ready,
        "checks": checks,
    }


# CLI 登录缺失的报错特征，命中则映射为 PROVIDER_AUTH_REQUIRED 供前端触发登录引导
_AUTH_REQUIRED_MARKERS = ("authentication required", "please use /login", "not authenticated")

# CLI `--help` 的 --model 描述里带完整支持清单（"Currently supported: (a, b, …)"）。
# 动态解析替代硬编码（CLI 升级清单会变）；解析失败回退 CLI_MODELS。
# 进程级缓存：help 调用秒级，不宜每个请求都跑。
_CLI_MODELS_CACHE: tuple[str, ...] | None = None
_CLI_MODELS_LOCK = threading.Lock()


def cli_supported_models() -> tuple[str, ...]:
    """CLI 当前支持的模型清单：解析 `--help` 的 --model 描述，失败回退内置清单。"""
    global _CLI_MODELS_CACHE
    with _CLI_MODELS_LOCK:
        if _CLI_MODELS_CACHE is not None:
            return _CLI_MODELS_CACHE
        models = CLI_MODELS
        resolved = _resolve_cli()
        if resolved:
            node, cli = resolved
            try:
                proc = subprocess.run(
                    [node, cli, "--help"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=30,
                    env=_build_env(), creationflags=_CREATE_NO_WINDOW,
                )
                match = re.search(r"Currently supported:\s*\(([^)]*)\)", proc.stdout)
                if match:
                    found = tuple(
                        item.strip() for item in match.group(1).split(",") if item.strip()
                    )
                    if found:
                        models = found
            except Exception:  # noqa: BLE001 - help 不可用时回退内置清单
                pass
        _CLI_MODELS_CACHE = models
        return models


def _is_auth_required(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _AUTH_REQUIRED_MARKERS)


class CodeBuddyCliProvider:
    """把本机 CodeBuddy CLI 的 stream-json 流解析成 AgentEvent 序列（同步生成器）。"""

    # 之前默认 1：模型任何一次工具调用（如 WebSearch）就会耗尽轮次，CLI 直接报
    # "Max turns (1) exceeded"，带工具的提问必挂。给足工具循环空间。
    def __init__(self, *, cwd: str = "", tools: tuple[str, ...] = (), max_turns: int = 6) -> None:
        self.cwd = cwd or ""
        self.tools = tools
        self.max_turns = max_turns

    @staticmethod
    def available() -> bool:
        return _resolve_cli() is not None

    def iter_events(
        self,
        model: str,
        messages: list[dict[str, object]],
        *,
        cancel_event=None,
        upstream_holder: dict[str, object] | None = None,
        timeout: float = _STREAM_TIMEOUT,
        reasoning_effort: str = "",
        resume: bool = False,
        session_id: str = "",
    ) -> Iterator[AgentEvent]:
        resolved = _resolve_cli()
        if resolved:
            node, cli = resolved
        else:
            yield AgentEvent(type="error", payload={
                "message": "未找到可用的 CodeBuddy CLI，请先安装 codebuddy-cli 插件并确保已登录",
                "code": "CLI_UNAVAILABLE",
            })
            return

        prompt = _render_prompt(messages)
        if not prompt.strip():
            yield AgentEvent(type="error", payload={
                "message": "对话内容为空", "code": "EMPTY_PROMPT",
            })
            return

        images = _image_blocks(messages)
        if images:
            # 带图对话：CLI 的纯文本 stdin 不支持图片，改走 stream-json 输入，
            # 图片块置于 transcript 文本之前（实测 deepseek-v4.1-flash 可正确识图）。
            stdin_text = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": [*images, {"type": "text", "text": prompt}]},
            }, ensure_ascii=False) + "\n"
        else:
            stdin_text = prompt

        command = self._build_command(
            node, cli, model, reasoning_effort,
            stream_input=bool(images), resume=resume, session_id=session_id,
        )
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self.cwd or None,
                env=_build_env(),
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001 - 启动失败统一转事件
            yield AgentEvent(type="error", payload={
                "message": f"启动 CodeBuddy CLI 失败：{exc}", "code": "CLI_UNAVAILABLE",
            })
            return

        if upstream_holder is not None:
            upstream_holder[_PROCESS_KEY] = proc

        # stdin 由独立线程写入：长 transcript 超过管道缓冲区时，主线程继续读 stdout 不阻塞
        def _write_stdin() -> None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except Exception:  # noqa: BLE001 - 进程已退出时写失败静默
                pass

        threading.Thread(target=_write_stdin, daemon=True).start()

        emitted_done = False
        deadline = time.monotonic() + timeout
        try:
            for line in proc.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    break
                if time.monotonic() > deadline:
                    yield AgentEvent(type="error", payload={
                        "message": "CodeBuddy CLI 响应超时", "code": "CLI_TIMEOUT",
                    })
                    emitted_done = True
                    break
                line = line.strip()
                if not line:
                    continue
                for event in self._parse_line(line, model):
                    yield event
                    if event.type in ("run_done", "error"):
                        emitted_done = True
                if emitted_done:
                    # 收到终态即退出读循环，不依赖 CLI 进程主动退出（避免 stdout 迟迟不 EOF）
                    break
        finally:
            if cancel_event is not None and cancel_event.is_set():
                self._terminate(proc)
            elif not emitted_done:
                # 流意外中断（进程崩溃/无 result）：补 error，避免编排层卡在 running
                self._terminate(proc)
                yield AgentEvent(type="error", payload={
                    "message": "CodeBuddy CLI 意外退出且无结果", "code": "CLI_STREAM_BROKEN",
                })
            if upstream_holder is not None:
                upstream_holder.pop(_PROCESS_KEY, None)

    def _build_command(
        self, node: str, cli: str, model: str, reasoning_effort: str,
        stream_input: bool = False, resume: bool = False, session_id: str = "",
    ) -> list[str]:
        command = [
            node, cli,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--mcp-config", "{}",
            "--max-turns", str(self.max_turns),
        ]
        # 会话续用：resume 用稳定 id 续接；否则用稳定 id 新建并落盘（供后续 resume）。
        # 只有 resume 时 CLI 自建历史，故不再传 --no-session-persistence。
        sid = session_id or uuid.uuid4().hex
        if resume:
            command += ["--resume", sid]
        else:
            command += ["--session-id", sid]
        if stream_input:
            command += ["--input-format", "stream-json"]
        if model:
            command += ["--model", model]
        if self.tools:
            command += ["--tools", ",".join(self.tools)]
        if self.cwd:
            command += ["--add-dir", self.cwd]
        if reasoning_effort:
            command += ["--effort", reasoning_effort]
        return command

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _parse_line(line: str, model: str) -> Iterator[AgentEvent]:
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        kind = str(obj.get("type") or "")
        if kind == "system":
            return
        if kind == "assistant":
            yield from _map_assistant(obj)
            return
        if kind == "user":
            yield from _map_user(obj)
            return
        if kind == "result":
            yield _map_result(obj, model)
            return
        if kind == "error":
            message = str(obj.get("message") or obj.get("error") or "CLI 返回错误")
            code = "PROVIDER_AUTH_REQUIRED" if _is_auth_required(message) else str(obj.get("code") or "CLI_ERROR")
            yield AgentEvent(type="error", payload={"message": message, "code": code})
            return


def _content_blocks(obj: dict[str, object]) -> list[object]:
    message = obj.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def _map_assistant(obj: dict[str, object]) -> Iterator[AgentEvent]:
    for block in _content_blocks(obj):
        btype = _block_type(block)
        if btype == "text":
            text = str(block.get("text") or "")
            if text:
                yield AgentEvent(type="message_delta", payload={"text": text})
        elif btype == "thinking":
            thinking = str(block.get("thinking") or block.get("text") or "")
            if thinking:
                yield AgentEvent(type="thinking_delta", payload={"text": thinking})
        elif btype == "tool_use":
            call_id = str(block.get("id") or "")
            name = str(block.get("name") or "")
            arguments = block.get("input")
            arguments_text = (
                json.dumps(arguments, ensure_ascii=False)
                if isinstance(arguments, (dict, list))
                else str(arguments or "")
            )
            yield AgentEvent(type="tool_call", payload={
                "id": call_id, "name": name, "arguments": arguments_text,
            })


def _map_user(obj: dict[str, object]) -> Iterator[AgentEvent]:
    for block in _content_blocks(obj):
        btype = _block_type(block)
        if btype == "tool_result":
            call_id = str(block.get("tool_use_id") or block.get("id") or "")
            result = _tool_result_text(block.get("content"))
            yield AgentEvent(type="tool_result", payload={"id": call_id, "result": result})


def _map_result(obj: dict[str, object], model: str) -> AgentEvent:
    if bool(obj.get("is_error")):
        message = str(obj.get("result") or obj.get("subtype") or "CLI 返回业务错误")
        code = "PROVIDER_AUTH_REQUIRED" if _is_auth_required(message) else "CLI_ERROR"
        return AgentEvent(type="error", payload={"message": message, "code": code})
    usage = obj.get("usage")
    return AgentEvent(type="run_done", payload={
        "finish_reason": "stop",
        "usage": cast(dict[str, object], usage) if isinstance(usage, dict) else None,
        "model": str(obj.get("model") or model or ""),
        "num_turns": obj.get("num_turns") if isinstance(obj.get("num_turns"), int) else None,
    })
