# -*- coding: utf-8 -*-
"""冻结构建 + 组装：三个 PyInstaller onedir 产物 -> src-tauri/binaries。

用法：
  python packaging/build.py [launcher|dashboard|cb|all] [--assemble]
  python packaging/build.py assemble [--sdk-cli PATH]     # 只组装
  python packaging/build.py verify                        # 只校验组装结果（CI 用）
  python packaging/build.py --sdk-cli PATH all --assemble # 显式指定 SDK CLI

SDK CLI（codebuddy-headless.exe）是桌面包的必需资源：构建时复制到
binaries/cb/bin/，随 NSIS 安装包一起分发；launcher 冻结态会注入
CODEBUDDY_CODE_PATH 指向它。第一次提供后缓存在 packaging/vendor/，
后续 assemble 无需再装 SDK 包（vendor/ 不入库）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT / "packaging"
CB_REPO = Path(
    os.environ.get("PLATEAU_CB_REPO")
    or PROJECT.parent.parent / "RuoYi-Vue3-FastAPI" / "cb"
)
DIST = PROJECT / "build" / "dist"
WORK = PROJECT / "build" / "work"
BINARIES = PROJECT / "desktop" / "src-tauri" / "binaries"
VENDOR = PACKAGING / "vendor"

SDK_CLI_NAME = "codebuddy-headless.exe"
SDK_CLI_DST = BINARIES / "cb" / "bin" / SDK_CLI_NAME

_TARGETS = ("launcher", "dashboard", "cb")

# 冻结态 command/cwd：dashboard.exe、cb.exe 与 launcher.exe 同属 binaries/ 下兄弟目录，
# cwd 相对 launcher.exe 所在目录（launcher._base_dir()）。
_FROZEN = {
    "dashboard": {"command": ["dashboard.exe"], "cwd": "../dashboard"},
    "cb": {
        "command": ["cb.exe", "-p", "{port}", "--config-dir", "{cb_config_dir}"],
        "cwd": "../cb",
    },
}


def write_frozen_services(dist_dir: Path) -> None:
    """从开发态 services.json 生成冻结版清单（替换 command/cwd）。"""
    src = json.loads((PROJECT / "desktop" / "services.json").read_text(encoding="utf-8"))
    out = dict(src)
    services = []
    for svc in src["services"]:
        item = dict(svc)
        frozen = _FROZEN.get(item["name"])
        if frozen is not None:
            item["command"] = frozen["command"]
            item["cwd"] = frozen["cwd"]
        services.append(item)
    out["services"] = services
    out["note"] = "冻结版清单：command/cwd 为 exe 相对路径，由 build.py 生成。"
    target = dist_dir / "services.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[build] 冻结版 services.json -> {target}", flush=True)


def build(name: str) -> None:
    env = dict(os.environ)
    if name == "cb":
        env["PLATEAU_CB_REPO"] = str(CB_REPO)
    print(f"[build] {name}: PyInstaller (dist={DIST / name}) ...", flush=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm",
            "--distpath", str(DIST),
            "--workpath", str(WORK / name),
            str(PACKAGING / f"{name}.spec"),
        ],
        cwd=str(PROJECT), env=env, check=True,
    )
    if name == "launcher":
        write_frozen_services(DIST / "launcher")
    print(f"[build] {name}: done -> {DIST / name}", flush=True)


def resolve_sdk_cli(explicit: str | None) -> Path:
    """定位 SDK CLI：显式参数 > 环境变量 > vendor 缓存 > 已安装的 SDK 包。

    找不到直接报错——CLI 是必需资源，静默跳过会产出「装得上但跑不起来」的包。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for key in ("PLATEAU_SDK_CLI", "CODEBUDDY_CODE_PATH"):
        raw = os.environ.get(key)
        if raw:
            candidates.append(Path(raw))
    candidates.append(VENDOR / SDK_CLI_NAME)
    try:
        import codebuddy_agent_sdk  # noqa: PLC0415
        candidates.append(
            Path(codebuddy_agent_sdk.__file__).resolve().parent / "bin" / SDK_CLI_NAME
        )
    except Exception:  # noqa: BLE001 - 未安装 SDK 时走其余候选
        pass

    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()

    searched = "\n  ".join(str(p) for p in candidates)
    raise RuntimeError(
        f"缺少必需资源 {SDK_CLI_NAME}。已尝试：\n  {searched}\n"
        "请用 --sdk-cli PATH 或 PLATEAU_SDK_CLI 指定；提供一次后会缓存到 packaging/vendor/。"
    )


def cache_sdk_cli(source: Path) -> Path:
    """缓存到 vendor/：assemble 会 rmtree 整个 binaries/cb，源在包内会先被删掉。"""
    VENDOR.mkdir(parents=True, exist_ok=True)
    cached = VENDOR / SDK_CLI_NAME
    if source.resolve() != cached.resolve():
        shutil.copy2(source, cached)
        print(f"[assemble] 缓存 SDK CLI: {source} -> {cached}", flush=True)
    return cached


def verify_assembly() -> bool:
    """组装结果强校验：三个 exe + 冻结清单 + 前端产物 + SDK CLI 全部齐备。"""
    required = [
        BINARIES / "launcher" / "launcher.exe",
        BINARIES / "launcher" / "services.json",
        BINARIES / "launcher" / "_internal" / "runtime" / "ports.py",
        BINARIES / "dashboard" / "dashboard.exe",
        BINARIES / "dashboard" / "_internal" / "frontend" / "dist" / "index.html",
        BINARIES / "dashboard" / "_internal" / "VERSION",
        BINARIES / "cb" / "cb.exe",
        SDK_CLI_DST,
    ]
    missing = [p for p in required if not p.is_file() or p.stat().st_size == 0]
    for path in missing:
        print(f"[verify] FAIL 缺失/空文件: {path}", flush=True)
    if missing:
        print(f"[verify] 组装不完整（{len(missing)} 项），不可打包", flush=True)
        return False
    print(f"[verify] OK {len(required)} 项资源齐备，SDK CLI={SDK_CLI_DST.stat().st_size} bytes", flush=True)
    return True


def assemble(sdk_cli: Path) -> bool:
    cached = cache_sdk_cli(sdk_cli)
    BINARIES.mkdir(parents=True, exist_ok=True)
    for name in _TARGETS:
        src = DIST / name
        if not src.is_dir():
            print(f"[assemble] 跳过 {name}（未构建，先 build）", flush=True)
            continue
        dst = BINARIES / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print(f"[assemble] {name} -> {dst}", flush=True)
    SDK_CLI_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, SDK_CLI_DST)
    print(f"[assemble] {SDK_CLI_NAME} -> {SDK_CLI_DST}（随 NSIS 包分发）", flush=True)
    return verify_assembly()


def parse_args(argv: list[str]) -> tuple[str | None, bool, list[str]]:
    explicit: str | None = None
    do_assemble = False
    targets: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--sdk-cli":
            if index + 1 >= len(argv):
                raise SystemExit("--sdk-cli 需要路径参数")
            explicit = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--sdk-cli="):
            explicit = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "--assemble":
            do_assemble = True
            index += 1
            continue
        targets.append(arg)
        index += 1
    return explicit, do_assemble, targets


def main() -> int:
    explicit, do_assemble, targets = parse_args(sys.argv[1:])

    if targets == ["verify"]:
        return 0 if verify_assembly() else 1
    if targets == ["assemble"]:
        try:
            return 0 if assemble(resolve_sdk_cli(explicit)) else 1
        except RuntimeError as error:
            print(f"[assemble] FAIL {error}", flush=True)
            return 2
    if not targets or "all" in targets:
        targets = list(_TARGETS)
    for name in targets:
        if name not in _TARGETS:
            print(f"[build] 未知目标 {name}（可选 {', '.join(_TARGETS)} / assemble / verify）")
            return 2
    for name in targets:
        build(name)
    if do_assemble:
        try:
            return 0 if assemble(resolve_sdk_cli(explicit)) else 1
        except RuntimeError as error:
            print(f"[assemble] FAIL {error}", flush=True)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
