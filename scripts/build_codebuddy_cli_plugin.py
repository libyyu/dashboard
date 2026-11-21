# -*- coding: utf-8 -*-
"""codebuddy-cli 插件打包：把 WorkBuddy 的 CLI（dist + bin）收集成 cli 插件。

用法：
  python scripts/build_codebuddy_cli_plugin.py [--version 2.0.0] [--out build/plugins] [--cli-src <dir>]

- 从 --cli-src（默认 LOCALAPPDATA/Programs/WorkBuddy/resources/app.asar.unpacked/cli）
  收集 dist/ 与 bin/
- 生成 plugin.json（kind=cli，runtimes=["node@22.22.2"]，files 回写 sha256）
- 打包成 codebuddy-cli-<version>.zip，可上传 registry /plugins/codebuddy-cli/<version>/

node 运行时不在本脚本打包：registry 只托管 runtimes/node/<version>/runtime.json
（含官方下载 URL + sha256），由安装流程从官方源拉取。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PLUGIN_ID = "codebuddy-cli"
DEFAULT_VERSION = "2.0.1"
# 共享 node 运行时基线（安装 codebuddy-cli 插件时自动确保已装）
NODE_RUNTIME = "node@22.23.5"

# 打包进插件的 CLI 相对子目录（相对 cli 源根）
# builtin/ 含 agent 模板 / mods / skills，product*.json 是产品配置（agent 注册的来源），
# 缺了会报 "Agent template is unavailable for get:default (registered=0)"。
INCLUDE = ("dist", "bin", "builtin")
# CLI 根目录下的单文件（package.json 用于包根定位）
ROOT_FILES = ("product.json", "package.json")
ROOT_GLOBS = ("product.*.json",)


def _default_cli_src() -> Path:
    local = os.environ.get("LOCALAPPDATA") or ""
    return Path(local) / "Programs" / "WorkBuddy" / "resources" / "app.asar.unpacked" / "cli"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(cli_src: Path, stage: Path) -> list[dict[str, str]]:
    """收集 dist/ 与 bin/ 到插件包 cli/ 下，返回 files 清单（含 sha256）。"""
    files: list[dict[str, str]] = []
    for rel in INCLUDE:
        src = cli_src / rel
        if not src.exists():
            raise FileNotFoundError(f"CLI 源缺少 {rel}：{src}")
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            rel_path = Path("cli") / path.relative_to(cli_src)
            dst = stage / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst)
            files.append({"path": rel_path.as_posix(), "sha256": _sha256(dst)})
    if not files:
        raise FileNotFoundError(f"CLI 源为空：{cli_src}")

    # CLI 根目录单文件：product.json / product.*.json / package.json
    seen = {Path(f["path"]).name for f in files}
    for name in ROOT_FILES:
        src = cli_src / name
        if src.is_file():
            dst = stage / name
            shutil.copy2(src, dst)
            files.append({"path": name, "sha256": _sha256(dst)})
            seen.add(name)
    for pattern in ROOT_GLOBS:
        for src in sorted(cli_src.glob(pattern)):
            if src.is_file() and src.name not in seen:
                dst = stage / src.name
                shutil.copy2(src, dst)
                files.append({"path": src.name, "sha256": _sha256(dst)})
                seen.add(src.name)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="codebuddy-cli 插件打包")
    _ = parser.add_argument("--version", default=DEFAULT_VERSION)
    _ = parser.add_argument("--out", default=str(PROJECT / "build" / "plugins"))
    _ = parser.add_argument("--cli-src", default="")
    args = parser.parse_args()

    cli_src = Path(args.cli_src or os.environ.get("CODEBUDDY_CLI_SRC") or _default_cli_src())
    if not cli_src.is_dir():
        print(
            f"[build_codebuddy_cli_plugin] FAIL CLI 源不存在：{cli_src}"
            f"（可设 --cli-src 或 CODEBUDDY_CLI_SRC）",
            file=sys.stderr,
        )
        return 2

    stage = Path(args.out) / f"{PLUGIN_ID}-{args.version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    files = _collect(cli_src, stage)

    manifest = {
        "schema": 1,
        "id": PLUGIN_ID,
        "name": "CodeBuddy CLI（本地）",
        "description": "本地 CodeBuddy CLI（subprocess 直调，非 sidecar 常驻）。依赖共享 node 运行时。",
        "version": args.version,
        "kind": "cli",
        "requires_core": "1.0.0",
        "requires": [],
        "provides": "model",
        "runtimes": [NODE_RUNTIME],
        "files": files,
    }
    manifest_path = stage / "plugin.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = Path(args.out) / f"{PLUGIN_ID}-{args.version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage))
    print(f"[build_codebuddy_cli_plugin] 打包完成：{zip_path}（{len(files)} 个文件）", flush=True)
    print(
        f"[build_codebuddy_cli_plugin] 上传到 registry：/plugins/{PLUGIN_ID}/{args.version}/",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
