# -*- coding: utf-8 -*-
"""桌面包 launcher：单 owner 管理多个 backend 服务。

借鉴 EvoFlow 的 sidecar 编排思路，用 Python 实现以便与 FastAPI / cb 同语言：

- 服务清单来自 desktop/services.json（仅 dashboard backend 与 cb，不含 RuoYi 9099）
- 端口按 profile 哈希派生并持久化（复用 backend/app/runtime/ports.py）
- 单 owner：launcher.lock + launcher.ready.json 双重判活，健康实例拒绝重复启动，stale 锁可接管
- fail-open：可选服务拉起或探活失败不阻塞其它服务
- 分层探活：第一轮全部 liveness，第二轮全部 readiness
- 有序关停：按清单逆序 terminate → wait → 清锁
- 基建日志：launcher 生命周期写结构化 JSONL；子进程 stdout/stderr 按日 append
  到同一日志目录（重启不丢历史），并注入 PLATEAU_LOGS_DIR 给所有服务

用法：
  python desktop/launcher.py --dry-run                    # 只打印解析结果
  python desktop/launcher.py --only dashboard --once      # 起单个服务自检后关停
  python desktop/launcher.py                             # 常驻，Ctrl+C 有序关停
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import importlib.util
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import date
from http.client import HTTPResponse
from pathlib import Path
from types import FrameType
from typing import Protocol, TextIO, TypedDict, cast

_SOURCE_ROOT = Path(__file__).resolve().parent           # desktop/
_SOURCE_PROJECT = _SOURCE_ROOT.parent                    # plateau-dashboard/
_SOURCE_BACKEND = _SOURCE_PROJECT / "backend"


def _is_frozen() -> bool:
    """PyInstaller 冻结态（onedir：sys.executable 即 launcher.exe）。"""
    return bool(getattr(sys, "frozen", False))


def _base_dir() -> Path:
    """services.json 与相对 cwd 的基准目录。

    源码态 = desktop/；冻结态 = launcher.exe 所在目录（binaries/launcher/），
    冻结版 services.json 与 dashboard/cb 相对路径都据此解析。
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return _SOURCE_ROOT


def _assembly_root() -> Path:
    """冻结态 = binaries/（dashboard.exe、cb.exe 的父目录）；源码态 = 项目根。"""
    if _is_frozen():
        return _base_dir().parent
    return _SOURCE_PROJECT


def _runtime_dir() -> Path:
    """backend/app/runtime 模块目录（launcher 按文件加载的 stdlib 模块）。

    源码态 = backend/app/runtime；冻结态 = PyInstaller --add-data 打入的
    _MEIPASS/runtime。
    """
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        return (Path(meipass) if meipass else _base_dir()) / "runtime"
    return _SOURCE_BACKEND / "app" / "runtime"


def _cb_home_default() -> Path:
    """cb 源码/冻结目录；冻结态指向包内 binaries/cb，源码态指向开发机 cb 仓库。"""
    if _is_frozen():
        return _assembly_root() / "cb"
    return _SOURCE_PROJECT.parent.parent / "RuoYi-Vue3-FastAPI" / "cb"


ROOT = _base_dir()  # 向后兼容别名：services.json / 相对 cwd 基准

if _is_frozen():
    # PyInstaller + CREATE_NO_WINDOW 下 sys.stderr 走系统 ANSI 代码页（中文 GBK），
    # 而桌面壳按 UTF-8 落盘/展示，崩溃栈会乱码。这里显式改成 UTF-8。
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None:
            with contextlib.suppress(Exception):
                _stream.reconfigure(encoding="utf-8", errors="replace")


class Service(TypedDict):
    name: str
    command: list[str]
    cwd: str
    health_path: str | None
    ready_path: str | None
    required: bool


class PortsModule(Protocol):
    BASE_PORT: int

    def profile_id(self) -> str: ...
    def home_dir(self) -> Path: ...
    def stable_port(self, key: str) -> int: ...
    def resolve_port(self, service: str) -> int: ...


class LogPolicyModule(Protocol):
    def logs_dir(self) -> Path: ...
    def max_bytes(self) -> int: ...
    def prune_daily_logs(self, root: Path) -> int: ...
    def daily_log_name(self, prefix: str, day: date | None = None, part: int = 0) -> str: ...
    def daily_log_path(
        self, prefix: str, day: date | None = None, part: int = 0, root: Path | None = None
    ) -> Path: ...


class LoggingSetupModule(Protocol):
    def configure_logging(
        self, service: str, log_dir: Path, path_factory: Callable[[str, date, int], Path],
        *, console: bool = ..., level: int = ..., max_file_bytes: int = ...,
    ) -> Path: ...
    def redact_text(self, value: str) -> str: ...
    def shutdown_logging(self) -> None: ...


_children: list[tuple[str, subprocess.Popen[bytes]]] = []
_lock_file: Path | None = None
_ready_file: Path | None = None
_logging_setup: LoggingSetupModule | None = None
_LOGGER = logging.getLogger("plateau.launcher")


def _load_runtime_module(name: str, filename: str) -> object:
    """按文件路径加载 runtime 模块，避免触发 app 包初始化。"""
    spec = importlib.util.spec_from_file_location(
        name, _runtime_dir() / filename
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 backend/app/runtime/{filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(object, module)


def _load_ports() -> PortsModule:
    return cast(PortsModule, _load_runtime_module("plateau_ports", "ports.py"))


_RESERVED_LOG_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
})
_FIELD_ALIASES = {"name": "service_name"}


def _event(event: str, level: int = logging.INFO, **fields: object) -> None:
    """launcher 结构化事件；控制台信息仍由各处的 print 负责。

    logging 不允许 extra 覆盖 LogRecord 内置字段，这里做别名 + 兜底重命名，
    避免新增字段时整个 launcher 因 KeyError 崩掉。
    """
    extra: dict[str, object] = {"event": event}
    for key, value in fields.items():
        safe = _FIELD_ALIASES.get(key, key)
        extra[safe if safe not in _RESERVED_LOG_KEYS else f"field_{safe}"] = value
    _LOGGER.log(level, event, extra=extra)


def _expand_str(value: str, ctx: dict[str, object]) -> str:
    for key, replacement in ctx.items():
        value = value.replace("{" + key + "}", str(replacement))
    return value


def _expand_command(values: list[str], ctx: dict[str, object]) -> list[str]:
    return [_expand_str(item, ctx) for item in values]


def _resolve_cwd(raw: str, ctx: dict[str, object]) -> Path:
    path = Path(_expand_str(raw, ctx))
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _pid_alive(pid: int) -> bool:
    """只读地探测进程是否存活。

    Windows 上 os.kill(pid, 0) 会走 TerminateProcess(handle, 0)，把目标进程
    直接杀掉并「成功返回」，绝不能用作存在性检查；改用 OpenProcess +
    GetExitCodeProcess（PROCESS_QUERY_LIMITED_INFORMATION，只读）。
    """
    if os.name != "nt":
        try:
            _ = os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False
    import ctypes  # noqa: PLC0415 - 仅 Windows 分支需要
    from ctypes import wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    finally:
        _ = kernel32.CloseHandle(handle)


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """原子替换，避免桌面壳读到写了一半的 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    _ = tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _lock_pid(path: Path) -> int:
    """兼容旧格式（纯 PID 文本）与新格式（{"pid": ...}）。"""
    payload = _read_json(path)
    if isinstance(payload.get("pid"), int):
        return int(payload["pid"])
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return -1


def _acquire_lock(path: Path, ready: Path) -> None:
    """单 owner：只有「旧 PID 存活 + 有它自己写的 ready 标记」才拒绝启动。

    旧版 / --once / 被强杀 / 卡在退出阶段的实例只会留下 lock，没有 ready，
    一律视为 stale 接管，避免桌面壳因一个死锁永远停在 loading 页。
    """
    global _lock_file
    old_pid = _lock_pid(path) if path.is_file() else -1
    if old_pid > 0 and old_pid != os.getpid() and _pid_alive(old_pid):
        if _read_json(ready).get("pid") == old_pid:
            raise RuntimeError(f"已有 launcher 在运行 (pid={old_pid})，拒绝重复启动")
        print(f"[WARN] 接管 stale launcher.lock (pid={old_pid} 仍存在但无 ready 标记)")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"pid": os.getpid(), "exe": Path(sys.executable).name})
    _lock_file = path


def _shutdown() -> None:
    # Windows 的 terminate 只杀直接子进程；uvicorn / cb 若再派生子进程会残留，
    # 占住端口导致下次 resolve_port 回退到别的端口。先按进程树强杀。
    if os.name == "nt":
        for _, proc in reversed(_children):
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    _ = subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW, timeout=5,
                    )
    for _, proc in reversed(_children):
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
    for name, proc in reversed(_children):
        try:
            _ = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        _event("service.exit", name=name, exit_code=proc.poll())
    if _lock_file is not None:
        with contextlib.suppress(Exception):
            _lock_file.unlink(missing_ok=True)
    if _ready_file is not None:
        with contextlib.suppress(Exception):
            _ready_file.unlink(missing_ok=True)
    _event("launcher.shutdown", children=len(_children))
    if _logging_setup is not None:
        _logging_setup.shutdown_logging()


def _pump_stdout(proc: subprocess.Popen[bytes], handle: TextIO, redact: Callable[[str], str]) -> None:
    """子进程 stdout 泵：按行 utf-8 解码 → 脱敏 → 落盘。

    子进程退出后管道 EOF，循环结束并关闭文件；写前统一过 redact_text，
    敏感信息不以明文落盘（展示层 /api/logs/tail 还有第二道兜底脱敏）。
    """
    stream = proc.stdout
    if stream is None:
        with contextlib.suppress(Exception):
            handle.close()
        return
    try:
        for raw in iter(stream.readline, b""):
            handle.write(redact(raw.decode("utf-8", errors="replace")))
            handle.flush()
    finally:
        with contextlib.suppress(Exception):
            handle.close()


def _probe(url: str, timeout: float = 1.0) -> bool:
    try:
        resp = cast(HTTPResponse, urllib.request.urlopen(url, timeout=timeout))
        with resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 探活失败一律视为未就绪
        return False


def _wait(url: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _probe(url):
            return True
        time.sleep(0.3)
    return False


def _on_sigint(_signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(0)


def main() -> int:
    global _logging_setup, _ready_file
    parser = argparse.ArgumentParser(description="Plateau 桌面包服务编排")
    _ = parser.add_argument("--dry-run", action="store_true", help="只打印解析结果，不启动、不写状态")
    _ = parser.add_argument("--only", default=None, help="只启动指定服务名")
    _ = parser.add_argument("--once", action="store_true", help="启动并探活后立即关停（自检用）")
    args = parser.parse_args()
    dry_run = cast(bool, args.dry_run)
    once = cast(bool, args.once)
    only = cast(str | None, args.only)

    ports = _load_ports()
    log_policy = cast(LogPolicyModule, _load_runtime_module("plateau_log_policy", "log_policy.py"))
    logging_setup = cast(
        LoggingSetupModule,
        _load_runtime_module("plateau_logging_setup", "logging_setup.py"),
    )
    profile = ports.profile_id()
    home = ports.home_dir()
    logs_root = log_policy.logs_dir()
    services = cast(
        list[Service],
        json.loads((ROOT / "services.json").read_text(encoding="utf-8"))["services"],
    )
    if only:
        services = [svc for svc in services if svc["name"] == only]

    cb_home = Path(os.environ.get("PLATEAU_CB_HOME") or str(_cb_home_default()))
    cb_config_dir = home / "profiles" / profile / "cb-config"

    # 先统一分配端口，构造共享环境（dashboard 的 ready 会检查 cb 是否可达）
    port_map: dict[str, int] = {}
    for svc in services:
        name = svc["name"]
        if name == "cb" and not cb_home.is_dir():
            print(f"[WARN] 跳过 cb：{cb_home} 不存在（可设 PLATEAU_CB_HOME）")
            continue
        port_map[name] = (
            ports.stable_port(f"{profile}:{name}")
            if dry_run
            else ports.resolve_port(name)
        )

    shared_env: dict[str, str] = dict(os.environ)
    shared_env["PLATEAU_PROFILE"] = profile
    shared_env["PLATEAU_HOME"] = str(home)
    shared_env["PLATEAU_LOGS_DIR"] = str(logs_root)
    shared_env["PLATEAU_SERVICES_JSON"] = str(ROOT / "services.json")
    # 子进程 stdout 统一 utf-8：Windows 默认用 GBK 写管道，读取端按 utf-8 解会乱码
    shared_env["PYTHONIOENCODING"] = "utf-8"
    shared_env["PYTHONUTF8"] = "1"
    shared_env["PORT"] = str(port_map.get("dashboard", ports.BASE_PORT))
    if "cb" in port_map:
        shared_env["PLATEAU_CB_HEALTH_URL"] = f"http://127.0.0.1:{port_map['cb']}/health"
        # 冻结态：SDK CLI 随包放在 cb/bin/，显式注入让 cb 的 _detect_cli 环境变量优先
        # 命中（目标机无开发机写死路径）。源码态该文件不存在，不注入，cb 自行探测。
        cli = _assembly_root() / "cb" / "bin" / "codebuddy-headless.exe"
        if cli.is_file():
            shared_env["CODEBUDDY_CODE_PATH"] = str(cli)
    else:
        _ = shared_env.pop("PLATEAU_CB_HEALTH_URL", None)
        _ = shared_env.pop("CODEBUDDY_CODE_PATH", None)

    if dry_run:
        for svc in services:
            name = svc["name"]
            if name not in port_map:
                continue
            dry_ctx: dict[str, object] = {
                "port": port_map[name], "cb_home": cb_home, "cb_config_dir": cb_config_dir,
            }
            cwd = _resolve_cwd(svc["cwd"], dry_ctx)
            cmd = _expand_command(svc["command"], dry_ctx)
            print(f"[DRY] {name}: port={port_map[name]} cwd={cwd} cmd={cmd}")
        print(f"[DRY] profile={profile} home={home} logs={logs_root} "
              f"cb_health={shared_env.get('PLATEAU_CB_HEALTH_URL')}")
        return 0

    _ = atexit.register(_shutdown)
    _ = signal.signal(signal.SIGINT, _on_sigint)
    profile_dir = home / "profiles" / profile
    ready_path = profile_dir / "launcher.ready.json"
    _acquire_lock(profile_dir / "launcher.lock", ready_path)

    # 桌面壳只认「本次 run 自己写的 ready」：run_id 由 Tauri 注入，避免读到
    # 上次运行残留的 runtime_state.json 端口而一直停在 loading。
    _write_json(ready_path, {
        "run_id": os.environ.get("PLATEAU_RUN_ID", ""),
        "pid": os.getpid(),
        "profile": profile,
        "ports": dict(port_map),
    })
    _ready_file = ready_path

    removed = log_policy.prune_daily_logs(logs_root)
    logging_setup.configure_logging(
        "launcher", logs_root, log_policy.daily_log_path,
        console=False, max_file_bytes=log_policy.max_bytes(),
    )
    _logging_setup = logging_setup
    _event(
        "launcher.start", profile=profile, home=str(home), logs=str(logs_root),
        services=json.dumps(sorted(port_map)), ports=json.dumps(port_map),
        pruned_logs=removed, once=once,
    )

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    started: list[tuple[Service, int, Path]] = []
    for svc in services:
        name = svc["name"]
        if name not in port_map:
            continue
        ctx: dict[str, object] = {
            "port": port_map[name], "cb_home": cb_home, "cb_config_dir": cb_config_dir,
        }
        if name == "cb":
            cb_config_dir.mkdir(parents=True, exist_ok=True)
        command = _expand_command(svc["command"], ctx)
        cwd = _resolve_cwd(svc["cwd"], ctx)
        # Windows：CreateProcess 不把 cwd 当搜索目录（lpApplicationName 相对时只搜
        # 调用进程目录/系统目录/PATH），冻结态 command[0] 是 cwd 下的相对 exe 名，
        # 需绝对化，否则报 [WinError 2]。源码态 command[0]="python" 不受影响（cwd 下无此文件）。
        first = Path(command[0])
        if not first.is_absolute() and (cwd / first).is_file():
            command[0] = str((cwd / first).resolve())
        env = dict(shared_env)
        # 按日 append：重启不丢历史；日志文件名符合 log_policy 命名，纳入保留策略
        log_path = logs_root / log_policy.daily_log_name(f"{name}-stdout")
        handle = open(log_path, "a", encoding="utf-8", errors="replace")
        try:
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                command, cwd=str(cwd), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as error:
            handle.close()
            print(f"[FAIL] {name} 启动失败: {error}")
            _event("service.spawn_failed", logging.ERROR, name=name, error=str(error))
            if svc["required"]:
                return 1
            continue
        _children.append((name, proc))
        # 泵线程：逐行 utf-8 解码 + 脱敏后落盘。必须是 daemon —— 若子进程
        # 残留（管道不 EOF），非 daemon 线程会吊住解释器，--once / 关停永远不返回，
        # 进而留下 stale lock。
        threading.Thread(
            target=_pump_stdout, args=(proc, handle, logging_setup.redact_text),
            name=f"stdout-{name}", daemon=True,
        ).start()
        started.append((svc, port_map[name], log_path))
        _event(
            "service.spawn", name=name, pid=proc.pid, port=port_map[name],
            cwd=str(cwd), command=json.dumps(command), stdout_log=str(log_path),
        )
        print(f"[OK] {name} 已拉起 pid={proc.pid} port={port_map[name]} log={log_path}")

    # 第一轮：全部 liveness；第二轮：全部 readiness
    results: dict[str, bool] = {}
    for phase, seconds in (("liveness", 30.0), ("readiness", 45.0)):
        for svc, port, _stdout_log in started:
            path = svc["health_path"] if phase == "liveness" else svc["ready_path"]
            if not path:
                continue
            url = f"http://127.0.0.1:{port}{path}"
            ok = _wait(url, seconds)
            results[f"{svc['name']}.{phase}"] = ok
            _event(
                "service.health", logging.INFO if ok else logging.WARNING,
                name=svc["name"], phase=phase, ok=ok, url=url,
            )
            print(f"[{'OK' if ok else 'FAIL'}] {svc['name']} {phase}: {url}")
            if not ok and svc["required"] and phase == "liveness":
                print(f"[FAIL] 必需服务 {svc['name']} 未存活")
                _event("launcher.abort", logging.ERROR, name=svc["name"], phase=phase)
                return 1

    if once:
        _event("launcher.once_done", results=json.dumps(results), ok=all(results.values()))
        print("[OK] --once 自检结束，准备关停")
        return 0 if all(results.values()) else 1

    _event("launcher.resident", pid=os.getpid())
    print(f"[OK] launcher 常驻中 (pid={os.getpid()})，Ctrl+C 有序关停")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
