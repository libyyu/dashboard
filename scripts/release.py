# -*- coding: utf-8 -*-
"""一键发版：bump → ci_cheap → 构建安装包 → build_update_manifest → 上传 registry。

把散落的手工发版步骤串成一条流水线，任何一步失败即停（非零退出）。

用法：
  python scripts/release.py --notes "修复 xxx"
  python scripts/release.py --notes "..." --skip-build    # 已有安装包，只跑守卫 + 上传
  python scripts/release.py --notes "..." --skip-upload   # 只升版本 + 构建，不发布

前置环境变量：
  TAURI_SIGNING_PRIVATE_KEY   签名私钥路径（npx tauri build 需要，否则产不出 .sig）
  PLATEAU_ADMIN_TOKEN         registry 管理 token（上传需要，否则管理端点 401）
  PLATEAU_REGISTRY_URL        registry 地址（默认 https://maxweili.devcloud.woa.com/registry）
  KEYSTORE_AUTH_ID / KEYSTORE_AUTH_SECRET / KEYSTORE_BIN
                              Keystore Authenticode 签名（见 scripts/keystore_sign.py）；
                              可选；配齐则签名，缺失则出未签名包

流程：
  1. bump_version.py --notes   升版本 + CHANGELOG + 同步 registry changelog.json
  2. ci_cheap.py               零依赖守卫（契约 / 解耦 / 更新通道不变量），失败即停
  3. 构建：frontend build → packaging/build.py all --assemble → 签 launcher/dashboard.exe
     → npx tauri build（signCommand 签主程序 / 卸载器 / 安装包）
  4. 校验构建产物齐备（setup.exe + .sig + Authenticode Valid）
  5. 上传 setup.exe + .sig → registry /api/admin/update/assets（自动生成 latest.json）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import keystore_sign  # 同目录脚本（python scripts/release.py 时 scripts/ 在 sys.path 上）

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DESKTOP = ROOT / "desktop"
NSIS_DIR = DESKTOP / "src-tauri" / "target" / "release" / "bundle" / "nsis"
DEFAULT_REGISTRY = "https://maxweili.devcloud.woa.com/registry"
PLATFORM = "windows-x86_64"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run(cmd: list[str], cwd: Path, shell: bool = False) -> None:
    print(f"\n[release] {' '.join(cmd)}  (cwd={cwd.name})", flush=True)
    proc = subprocess.run(
        cmd, cwd=str(cwd), shell=shell,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if proc.stdout:
        sys.stdout.write(_decode(proc.stdout))
        sys.stdout.flush()
    if proc.returncode != 0:
        raise SystemExit(f"[release] 步骤失败（exit {proc.returncode}）：{' '.join(cmd)}")


def read_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def setup_path(version: str) -> Path:
    return NSIS_DIR / f"Plateau_{version}_x64-setup.exe"


def bump(notes: str) -> None:
    _run([sys.executable, str(ROOT / "scripts" / "bump_version.py"), "--notes", notes], ROOT)


def guard() -> None:
    _run([sys.executable, str(ROOT / "scripts" / "ci_cheap.py")], ROOT)


def _sign_override_config() -> Path:
    """生成 tauri --config 覆盖文件：让打包器用 keystore_sign.py 签主程序 / NSIS 插件 / 卸载器 / 安装包。

    不写进 tauri.conf.json：本机 dev 构建没有 Keystore 凭据，也不该被签名步骤卡住。
    """
    override = {
        "bundle": {"windows": {"signCommand": {
            "cmd": sys.executable,
            "args": [str(ROOT / "scripts" / "keystore_sign.py"), "%1"],
        }}}
    }
    path = ROOT / "build" / "tauri.sign.override.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(override, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _resource_exes() -> list[Path]:
    """随包分发的 PyInstaller 产物（tauri 只签主程序，不签 resources）。"""
    base = DESKTOP / "src-tauri" / "binaries"
    return sorted(p for sub in ("launcher", "dashboard") for p in (base / sub).glob("*.exe"))


def build(signed: bool) -> None:
    if not os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
        raise SystemExit("[release] 未设置 TAURI_SIGNING_PRIVATE_KEY（npx tauri build 需要签名私钥）")
    _run(["npm", "run", "build"], FRONTEND, shell=True)
    _run([sys.executable, str(ROOT / "packaging" / "build.py"), "all", "--assemble"], ROOT)
    _run([sys.executable, str(ROOT / "packaging" / "build.py"), "verify"], ROOT)
    tauri_cmd = ["npx", "tauri", "build"]
    if signed:
        exes = _resource_exes()
        if not exes:
            raise SystemExit("[release] binaries/ 下没有找到 launcher/dashboard 可执行文件")
        print(f"\n[release] 签名随包可执行文件：{[p.name for p in exes]}", flush=True)
        keystore_sign.sign(exes)
        tauri_cmd += ["--config", str(_sign_override_config())]
    _run(tauri_cmd, DESKTOP, shell=True)


def verify_artifacts(version: str, signed: bool) -> None:
    """校验构建产物齐备（setup.exe + .sig）。不校验本地 latest.json：正式发布由 registry 自动生成。"""
    setup = setup_path(version)
    sig = Path(str(setup) + ".sig")
    for path, label in ((setup, "安装包"), (sig, "签名")):
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"[release] 缺少{label}：{path}")
        print(f"[release] {label}齐备：{path.name}（{path.stat().st_size} B）")
    status = keystore_sign.signature_status(setup)
    print(f"[release] 安装包 Authenticode：{status}")
    if signed and status != "Valid":
        raise SystemExit("[release] 安装包未通过 Authenticode 校验，拒绝发布")


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")
    for name, (filename, data) in files.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        body += data
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return boundary, bytes(body)


def upload(version: str) -> None:
    token = os.environ.get("PLATEAU_ADMIN_TOKEN", "").strip()
    if not token:
        raise SystemExit("[release] 未设置 PLATEAU_ADMIN_TOKEN（上传 registry 需要）")
    registry = os.environ.get("PLATEAU_REGISTRY_URL", "").strip() or DEFAULT_REGISTRY

    setup = setup_path(version)
    sig = Path(str(setup) + ".sig")
    for path, label in ((setup, "安装包"), (sig, "签名")):
        if not path.is_file():
            raise SystemExit(f"[release] 缺少{label}：{path}")

    notes = ""
    changelog = ROOT / "CHANGELOG.md"
    if changelog.is_file():
        text = changelog.read_text(encoding="utf-8")
        match = re.search(rf"(?ms)^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text)
        if match:
            notes = "；".join(
                line.strip().lstrip("-").strip()
                for line in match.group(1).splitlines()
                if line.strip() and not line.strip().startswith("###")
            )

    boundary, body = _multipart(
        {"version": version, "notes": notes},
        {"setup": (setup.name, setup.read_bytes()), "sig": (sig.name, sig.read_bytes())},
    )
    url = f"{registry.rstrip('/')}/api/admin/update/assets"
    req = Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    print(f"\n[release] 上传 → {url}", flush=True)
    try:
        with urlopen(req, timeout=300.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[release] 上传失败：{exc}") from exc
    print(f"[release] 发布完成：version={payload.get('version')}")
    print(f"[release] 下载地址：{payload.get('url')}")


def main() -> int:
    # Windows 控制台默认 GBK，构建工具输出含 ✓/← 等字符会触发 UnicodeEncodeError
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="一键发版")
    _ = parser.add_argument("--notes", default="", help="本次更新内容（升版本 + CHANGELOG 用；--skip-bump 时忽略）")
    _ = parser.add_argument("--skip-bump", action="store_true", help="跳过升版本，用当前 VERSION 构建上传")
    _ = parser.add_argument("--skip-build", action="store_true", help="跳过构建安装包（用已有产物）")
    _ = parser.add_argument("--skip-upload", action="store_true", help="只升版本 + 构建，不上传 registry")
    args = parser.parse_args()

    # Authenticode 签名可选：配了 Keystore 凭据就签，没配就出未签名包（iOA 需手动放行）
    missing = keystore_sign.missing_prereqs()
    signed = not missing
    print(f"[release] Authenticode 签名：{'启用' if signed else '跳过（' + ', '.join(missing) + '）'}")

    if not args.skip_bump:
        if not args.notes:
            raise SystemExit("[release] 未指定 --skip-bump 时必须给 --notes")
        bump(args.notes)
    guard()
    if not args.skip_build:
        build(signed)
    verify_artifacts(read_version(), signed)
    if not args.skip_upload:
        upload(read_version())
    print("\n[release] 全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
