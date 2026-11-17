# -*- coding: utf-8 -*-
"""冻结构建 + 组装：三个 PyInstaller onedir 产物 -> src-tauri/binaries。

用法：
  python packaging/build.py [launcher|dashboard|cb|all]   # 冻结（launcher 会生成冻结版 services.json）
  python packaging/build.py assemble                      # 组装到 desktop/src-tauri/binaries + 复制 CLI
  python packaging/build.py [target] --assemble           # 冻结后立即组装
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


def _sdk_cli() -> Path:
    """SDK 的 codebuddy-headless.exe：优先从已安装包定位，其次环境变量覆盖。"""
    try:
        import codebuddy_agent_sdk  # noqa: PLC0415
        cli = Path(codebuddy_agent_sdk.__file__).resolve().parent / "bin" / "codebuddy-headless.exe"
        if cli.is_file():
            return cli
    except Exception:  # noqa: BLE001
        pass
    override = os.environ.get("CODEBUDDY_CODE_PATH")
    if override:
        return Path(override)
    return BINARIES / "cb" / "bin" / "codebuddy-headless.exe"


def assemble() -> None:
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
    cli = _sdk_cli()
    if cli.is_file():
        cli_dst = BINARIES / "cb" / "bin" / "codebuddy-headless.exe"
        cli_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cli, cli_dst)
        print(f"[assemble] codebuddy-headless.exe -> {cli_dst}", flush=True)
    else:
        print(f"[assemble] WARN: 未找到 codebuddy-headless.exe（{cli}）", flush=True)


def main() -> int:
    args = sys.argv[1:]
    do_assemble = "--assemble" in args
    targets = [a for a in args if a not in ("--assemble",)]
    if "assemble" in targets and len(targets) == 1:
        assemble()
        return 0
    if not targets or "all" in targets:
        targets = list(_TARGETS)
    for name in targets:
        if name not in _TARGETS:
            print(f"[build] 未知目标 {name}（可选 {', '.join(_TARGETS)}）")
            return 2
    for name in targets:
        build(name)
    if do_assemble:
        assemble()
    return 0


if __name__ == "__main__":
    sys.exit(main())
