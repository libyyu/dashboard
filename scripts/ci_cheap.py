# -*- coding: utf-8 -*-
"""cheap CI lane：stdlib-only 守卫，push / pre-commit 秒级跑完。

借鉴 EvoFlow 的「push 只跑零依赖守卫，重活放 workflow_dispatch」：
避免「CI 长期红 → 没人看门禁」。本脚本可直接作为 pre-commit 或 CI 的 cheap lane。

检查项：
1. 版本一致性（VERSION / package.json / desktop/services.json）
2. 车道分类不变量（未知 /api/* 默认 DATA、Control 永不 shed、BEST_EFFORT 过载 shed）
3. 端口派生稳定且落在 8100..8999
4. 服务清单不含 9099 / RuoYi 后端
5. 所有 Python 源文件语法可编译

用法：python scripts/ci_cheap.py  （全部通过退出 0）
"""
from __future__ import annotations

import ast
import compileall
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "app" / "runtime"))
sys.path.insert(0, str(ROOT / "scripts"))

import log_policy  # noqa: E402
import ports  # noqa: E402
import sync_version  # noqa: E402
from logging_setup import redact_headers, redact_text  # noqa: E402
from traffic_lanes import Lane, classify_path, overload_action  # noqa: E402

_STDLIB_ONLY = frozenset({
    "__future__", "collections", "contextlib", "dataclasses", "datetime", "json",
    "logging", "os", "pathlib", "re", "sys", "traceback", "types", "typing",
})

_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"[OK] {name}")
    else:
        _failures.append(name)
        print(f"[FAIL] {name}" + (f": {detail}" if detail else ""))


def main() -> int:
    services_doc = sync_version.read_services_doc()
    version_problems = sync_version.version_problems()
    check("版本一致性（VERSION + 5 处）", not version_problems, "; ".join(version_problems))

    check("未知 /api/* 默认 DATA", classify_path("/api/brand-new-slow-thing").lane is Lane.DATA)
    check("health 归 CONTROL", classify_path("/health/ready").lane is Lane.CONTROL)
    check("登录归 CONTROL", classify_path("/auth/status").lane is Lane.CONTROL)
    check("流式归 STREAM", classify_path("/v1/chat/completions").lane is Lane.STREAM)
    check("CONTROL 过载也不 shed", overload_action(Lane.CONTROL, True) == "allow")
    check("STREAM 过载也不 shed", overload_action(Lane.STREAM, True) == "allow")
    check("BEST_EFFORT 过载被 shed", overload_action(Lane.BEST_EFFORT, True) == "shed")
    check("BEST_EFFORT 不过载放行", overload_action(Lane.BEST_EFFORT, False) == "allow")

    sample = ports.stable_port("default:dashboard")
    check("端口落在 8100..8999", ports.BASE_PORT <= sample <= ports.BASE_PORT + ports.PORT_RANGE - 1, str(sample))
    check("端口派生稳定", ports.stable_port("default:dashboard") == sample)
    check("不同服务端口不同", ports.stable_port("default:dashboard") != ports.stable_port("default:cb"))

    services = services_doc["services"]
    names = sorted(s["name"] for s in services)
    check("服务清单仅 dashboard + cb", names == ["cb", "dashboard"], str(names))
    # 只检查服务配置本身，说明文字里提到「不含 9099」不算命中
    spec_text = json.dumps(services, ensure_ascii=False).lower()
    check("服务配置不含 9099 / RuoYi 后端", "9099" not in spec_text and "ruoyi" not in spec_text)

    check("日志车道 BEST_EFFORT", classify_path("/api/logs/tail").lane is Lane.BEST_EFFORT)
    derived_logs = (ports.home_dir() / "profiles" / ports.profile_id() / "logs").resolve()
    check("日志目录与 ports 同源",
          log_policy.logs_dir() == derived_logs or bool(os.environ.get("PLATEAU_LOGS_DIR")))
    check("日志命名可解析", log_policy.parse_daily_name("dashboard-2026-11-17.2.log") is not None)
    check("日志路径防穿越",
          not log_policy.is_safe_log_name("../secret.log")
          and log_policy.is_safe_log_name("dashboard-2026-11-17.log"))
    check("保留天数默认 14 且有上界",
          log_policy.retention_days() <= log_policy.MAX_RETENTION_DAYS
          and log_policy.DEFAULT_RETENTION_DAYS == 14)
    check("Bearer 脱敏", "abc.def" not in redact_text("Authorization: Bearer abc.def"))
    check("ck_ 脱敏", "[REDACTED]" in redact_text("api_key=ck_abcdefgh12345"))
    check("敏感 header 脱敏",
          redact_headers({"Authorization": "ck_abcdefgh", "X-Trace": "ok"})["Authorization"]
          == "[REDACTED]")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        old = tmp_root / log_policy.daily_log_name("probe", date(2000, 1, 1))
        _ = old.write_text("x", encoding="utf-8")
        recent = tmp_root / log_policy.daily_log_name("probe")
        _ = recent.write_text("x", encoding="utf-8")
        removed = log_policy.prune_daily_logs(tmp_root)
        check("按日保留策略", removed == 1 and not old.exists() and recent.exists())

    for rel in (
        "backend/app/runtime/log_policy.py",
        "backend/app/runtime/logging_setup.py",
        "backend/app/core/storage.py",
        "backend/app/core/agent.py",
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        check(f"{Path(rel).name} 仅依赖 stdlib", imported <= _STDLIB_ONLY, str(sorted(imported)))

    # 前后端契约：两侧各自独立验证，任一侧不得 import 对方代码
    contract = json.loads((ROOT / "contracts" / "api.json").read_text(encoding="utf-8"))
    endpoints = contract["endpoints"]
    backend_files = (
        ROOT / "backend" / "app" / "main.py",
        ROOT / "backend" / "app" / "runtime" / "health.py",
        ROOT / "backend" / "app" / "runtime" / "log_routes.py",
        ROOT / "backend" / "app" / "routers" / "runtime.py",
    )
    backend_src = "\n".join(
        path.read_text(encoding="utf-8") for path in backend_files if path.is_file()
    )
    main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    core_src = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "backend" / "app" / "core").glob("*.py"))
    )
    errors_src = (ROOT / "backend" / "app" / "core" / "errors.py").read_text(encoding="utf-8")
    agent_src = (ROOT / "backend" / "app" / "core" / "agent.py").read_text(encoding="utf-8")
    contract_ts = (ROOT / "frontend" / "src" / "api" / "contract.ts").read_text(encoding="utf-8")
    http_ts = (ROOT / "frontend" / "src" / "api" / "http.ts").read_text(encoding="utf-8")
    vite_cfg = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    def registered(path: str) -> bool:
        if path in backend_src:
            return True
        return "/" + path.rsplit("/", 1)[-1] in backend_src

    check("契约端点都在后端注册",
          all(registered(e["path"]) for e in endpoints if e["implemented"]))
    check("契约端点都在前端常量里", all(e["path"] in contract_ts for e in endpoints))
    check("契约车道与 classify_path 一致",
          all(classify_path(e["path"]).lane.value == e["lane"] for e in endpoints))
    check("错误码契约双侧齐备", all(
        code in errors_src or code in http_ts or code in backend_src
        for code in contract["error_codes"]
    ))
    check("前后端各自独立验证",
          contract["architecture"]["validation"]["independent"] is True
          and "VITE_API_MOCK" in http_ts)
    check("前端不 import 后端代码",
          "from app." not in contract_ts and "backend/" not in http_ts)
    check("前端统一错误与 request-id", all(
        token in http_ts for token in (
            "x-request-id", "AbortController", "GATEWAY_OVERLOAD_SHED",
            "VITE_API_MOCK", "retry-after",
        )
    ))
    check("SPA 深链回退", "SPAStaticFiles" in main_src and "index.html" in main_src)
    check("开发代理不写死 8899", "8899" not in vite_cfg and "VITE_API_TARGET" in vite_cfg)

    # 装配层 → 服务依赖方向：launcher 注入服务清单，backend 只读环境变量，不反向硬编码路径
    runtime_router_src = (ROOT / "backend" / "app" / "routers" / "runtime.py").read_text(encoding="utf-8")
    launcher_src = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    check("运行时聚合不硬编码 desktop 路径",
          "PLATEAU_SERVICES_JSON" in runtime_router_src and "parents[3]" not in runtime_router_src)
    check("launcher 注入服务清单环境变量", "PLATEAU_SERVICES_JSON" in launcher_src)
    check("launcher stdout 走 utf-8 且写前脱敏",
          "PYTHONIOENCODING" in launcher_src and "redact_text" in launcher_src)

    # 数据库可移植：core 只放端口，不出现方言 SQL 或具体驱动
    forbidden_sql = (
        "AUTOINCREMENT", "INSERT OR IGNORE", "INSERT OR REPLACE",
        "datetime('now')", "rowid", "PRAGMA",
    )
    check("存储端口不含方言 SQL", not any(token in core_src for token in forbidden_sql))
    check("数据库走 DATABASE_URL 且不硬编码驱动",
          "DATABASE_URL" in core_src and "import sqlite3" not in core_src
          and "import sqlalchemy" not in core_src and "import psycopg" not in core_src)
    check("Agent 端口与扩展点齐备", all(
        name in agent_src for name in (
            "ModelProvider", "ToolRegistry", "SkillRegistry", "SessionStore",
            "Orchestrator", "AgentEvent", "MCPToolAdapter", "CBOpenAIProvider",
        )
    ))

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check("日志被 gitignore", "*.log" in gitignore and "logs/" in gitignore)
    check("更新产物被 gitignore", "latest.json" in gitignore and "*.sig" in gitignore)

    # 更新通道不变量：公钥必须是真值；http 端点只允许 loopback，
    # 防止 dangerousInsecureTransportProtocol 被带到公网部署。
    tauri_cfg = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    updater_cfg = tauri_cfg.get("plugins", {}).get("updater", {})
    pubkey = str(updater_cfg.get("pubkey", ""))
    endpoints = [str(u) for u in updater_cfg.get("endpoints", [])]
    insecure = bool(updater_cfg.get("dangerousInsecureTransportProtocol"))
    loopback = {"127.0.0.1", "localhost", "::1"}
    check("updater 公钥已配置", len(pubkey) >= 80 and "PLACEHOLDER" not in pubkey.upper())
    check("updater 生成签名产物", tauri_cfg.get("bundle", {}).get("createUpdaterArtifacts") is True)
    check("updater 端点非空", bool(endpoints))
    check("insecure 开关仅限本机联调",
          not insecure or all(
              urlparse(u).scheme == "http" and (urlparse(u).hostname or "") in loopback
              for u in endpoints
          ), str(endpoints))
    check("NSIS 更新为 passive 模式", updater_cfg.get("windows", {}).get("installMode") == "passive")

    updater_src = (ROOT / "desktop" / "src-tauri" / "src" / "updater.rs").read_text(encoding="utf-8")
    check("更新走三段式且不覆盖退出钩子",
          ".on_before_exit(" not in updater_src
          and "spawn::stop_launcher()" in updater_src
          and "report_failure(" not in updater_src)
    lib_src = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    check("后台检查独立于 boot 链路", "updater::schedule" in lib_src and "检查更新" in lib_src)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        syntax_ok = True
        for folder in (ROOT / "backend", ROOT / "desktop", ROOT / "scripts"):
            syntax_ok = compileall.compile_dir(str(folder), quiet=1) and syntax_ok
    check("Python 语法编译", syntax_ok, buffer.getvalue()[-300:])

    print("-" * 48)
    if _failures:
        print(f"[FAIL] cheap lane 未通过：{len(_failures)} 项")
        return 1
    print("[OK] cheap lane 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
