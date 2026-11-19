# -*- coding: utf-8 -*-
"""冻结构建 + 组装：两个 PyInstaller onedir 产物 -> src-tauri/binaries。

用法：
  python packaging/build.py [launcher|dashboard|all] [--assemble]
  python packaging/build.py assemble     # 只组装
  python packaging/build.py verify        # 只校验组装结果（CI 用）

cb 已外置为 sidecar 插件（见 scripts/build_cb_plugin.py），不再随桌面包内置。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT / "packaging"
DIST = PROJECT / "build" / "dist"
WORK = PROJECT / "build" / "work"
BINARIES = PROJECT / "desktop" / "src-tauri" / "binaries"

_TARGETS = ("launcher", "dashboard")

# 冻结态 command/cwd：dashboard.exe 与 launcher.exe 同属 binaries/ 下兄弟目录，
# cwd 相对 launcher.exe 所在目录（launcher._base_dir()）。
_FROZEN = {
    "dashboard": {"command": ["dashboard.exe"], "cwd": "../dashboard"},
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
    print(f"[build] {name}: PyInstaller (dist={DIST / name}) ...", flush=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm",
            "--distpath", str(DIST),
            "--workpath", str(WORK / name),
            str(PACKAGING / f"{name}.spec"),
        ],
        cwd=str(PROJECT), check=True,
        # PyInstaller 日志走 stderr；并入 stdout 后 CI / IDE 能持续看到心跳，
        # 也避免只有一路输出时长时间「无输出」被误判为卡死。
        stderr=subprocess.STDOUT,
    )
    if name == "launcher":
        write_frozen_services(DIST / "launcher")
    print(f"[build] {name}: done -> {DIST / name}", flush=True)


def verify_assembly() -> bool:
    """组装结果强校验：两个 exe + 冻结清单 + 前端产物全部齐备。"""
    required = [
        BINARIES / "launcher" / "launcher.exe",
        BINARIES / "launcher" / "services.json",
        BINARIES / "launcher" / "_internal" / "runtime" / "ports.py",
        BINARIES / "dashboard" / "dashboard.exe",
        BINARIES / "dashboard" / "_internal" / "frontend" / "dist" / "index.html",
        BINARIES / "dashboard" / "_internal" / "VERSION",
    ]
    missing = [p for p in required if not p.is_file() or p.stat().st_size == 0]
    for path in missing:
        print(f"[verify] FAIL 缺失/空文件: {path}", flush=True)
    if missing:
        print(f"[verify] 组装不完整（{len(missing)} 项），不可打包", flush=True)
        return False
    print(f"[verify] OK {len(required)} 项资源齐备", flush=True)
    return True


def assemble() -> bool:
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
    return verify_assembly()


def parse_args(argv: list[str]) -> tuple[bool, list[str]]:
    do_assemble = False
    targets: list[str] = []
    for arg in argv:
        if arg == "--assemble":
            do_assemble = True
        else:
            targets.append(arg)
    return do_assemble, targets


def main() -> int:
    do_assemble, targets = parse_args(sys.argv[1:])

    if targets == ["verify"]:
        return 0 if verify_assembly() else 1
    if targets == ["assemble"]:
        return 0 if assemble() else 1
    if not targets or "all" in targets:
        targets = list(_TARGETS)
    for name in targets:
        if name not in _TARGETS:
            print(f"[build] 未知目标 {name}（可选 {', '.join(_TARGETS)} / assemble / verify）")
            return 2
    for name in targets:
        build(name)
    if do_assemble:
        return 0 if assemble() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
