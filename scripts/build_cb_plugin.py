# -*- coding: utf-8 -*-
"""cb 插件打包：把 cb 仓库（server.py + cbopenai/）打包成 sidecar 插件。

用法：
  python scripts/build_cb_plugin.py [--version 2.0.0] [--out build/plugins] [--with-venv]

- 从 PLATEAU_CB_REPO（默认 ../RuoYi-Vue3-FastAPI/cb）收集 server.py + cbopenai/
- 生成 plugin.json（files 回写 sha256，service 用 launcher 占位符）
- 打包成 cb-openai-<version>.zip，可上传 registry /plugins/cb-openai/<version>/
- --with-venv：在包体目录建 venv 并 pip install codebuddy_agent_sdk（跨平台可选，
  通常由 registry 侧在目标平台构建，本地可选）

cb 已彻底外置：桌面包（packaging/build.py）不再内置 cb，改为从 registry 安装本插件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_CB_REPO = PROJECT.parent.parent / "RuoYi-Vue3-FastAPI" / "cb"

PLUGIN_ID = "cb-openai"
DEFAULT_VERSION = "2.0.0"

# 打包进插件的源文件/目录（相对 cb 仓库根；cbopenai 递归收集）
INCLUDE = ("server.py", "cbopenai")

_MANIFEST_BASE = {
    "schema": 1,
    "id": PLUGIN_ID,
    "name": "CB大模型",
    "description": "OpenAI 兼容大模型服务（/v1/models、/v1/chat/completions、/auth/*），sidecar 常驻。",
    "kind": "sidecar",
    "requires_core": "1.0.0",
    "requires": [],
    "provides": "model",
    "service": {
        "command": [
            "{python}", "server.py", "-p", "{port}", "--host", "127.0.0.1",
            "--config-dir", "{config_dir}",
        ],
        "cwd": "{plugin_dir}",
        "env": {"CB_REQUEST_TIMEOUT": "1800", "CB_IDLE_TIMEOUT": "300"},
        "health_path": "/health",
        "ready_path": "/health",
        "required": False,
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(cb_repo: Path, stage: Path) -> list[dict[str, str]]:
    """收集 server.py + cbopenai/ 到 staging 目录，返回 files 清单（含 sha256）。"""
    files: list[dict[str, str]] = []
    for rel in INCLUDE:
        src = cb_repo / rel
        if src.is_file():
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            files.append({"path": rel, "sha256": _sha256(dst)})
        elif src.is_dir():
            for path in sorted(src.rglob("*")):
                if path.is_file():
                    rel_path = path.relative_to(cb_repo).as_posix()
                    dst = stage / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dst)
                    files.append({"path": rel_path, "sha256": _sha256(dst)})
        else:
            raise FileNotFoundError(f"cb 仓库缺少 {rel}：{src}")
    return files


def _build_venv(stage: Path) -> list[dict[str, str]]:
    """可选：在包体目录建 venv 并安装 codebuddy_agent_sdk（自带 CLI），返回 venv 文件清单。"""
    venv = stage / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    subprocess.run([str(pip), "install", "codebuddy_agent_sdk"], check=True)
    files: list[dict[str, str]] = []
    for path in sorted(venv.rglob("*")):
        if path.is_file():
            rel_path = path.relative_to(stage).as_posix()
            files.append({"path": rel_path, "sha256": _sha256(path)})
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="cb 插件打包")
    _ = parser.add_argument("--version", default=DEFAULT_VERSION)
    _ = parser.add_argument("--out", default=str(PROJECT / "build" / "plugins"))
    _ = parser.add_argument("--with-venv", action="store_true",
                            help="在包体建 venv 并安装 SDK（跨平台可选，通常由 registry 侧构建）")
    args = parser.parse_args()

    cb_repo = Path(os.environ.get("PLATEAU_CB_REPO") or DEFAULT_CB_REPO)
    if not cb_repo.is_dir():
        print(f"[build_cb_plugin] FAIL cb 仓库不存在：{cb_repo}（可设 PLATEAU_CB_REPO）",
              file=sys.stderr)
        return 2

    stage = Path(args.out) / f"{PLUGIN_ID}-{args.version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    files = _collect(cb_repo, stage)
    if args.with_venv:
        files.extend(_build_venv(stage))

    manifest = dict(_MANIFEST_BASE)
    manifest["version"] = args.version
    manifest["files"] = files
    manifest_path = stage / "plugin.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = Path(args.out) / f"{PLUGIN_ID}-{args.version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage))
    print(f"[build_cb_plugin] 打包完成：{zip_path}（{len(files)} 个文件）", flush=True)
    print(f"[build_cb_plugin] 上传到 registry：/plugins/{PLUGIN_ID}/{args.version}/", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
