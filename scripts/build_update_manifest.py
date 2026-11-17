# -*- coding: utf-8 -*-
"""生成 Tauri updater 的静态 manifest（latest.json）。

版本号只从 VERSION 读，不接受手工传参，避免「安装包 1.0.0 / manifest 1.0.1」。
默认面向本机更新服务器（http://127.0.0.1:17865），后续部署正式服务时改 --url / --base-url。

用法：
  python scripts/build_update_manifest.py                     # 用默认 NSIS 产物生成 latest.json
  python scripts/build_update_manifest.py --notes "修复冷启动"
  python scripts/build_update_manifest.py --verify            # 只校验已有 latest.json

本地服务器（产物与 latest.json 同目录）：
  python -m http.server 17865 --bind 127.0.0.1 --directory desktop/src-tauri/target/release/bundle/nsis
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sync_version  # noqa: E402

NSIS_DIR = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
DEFAULT_BASE_URL = "http://127.0.0.1:17865"
PLATFORM = "windows-x86_64"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def read_signature(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"签名文件不存在：{path}（先带 TAURI_SIGNING_PRIVATE_KEY 跑 npx tauri build）")
    raw = "".join(path.read_text(encoding="utf-8").split())
    if not raw:
        raise RuntimeError(f"签名文件为空：{path}")
    try:
        _ = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"签名不是合法 base64：{path} ({exc})") from exc
    return raw


def validate_url(url: str, setup_name: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"更新包 URL 必须是 http/https：{url}")
    if not parsed.netloc or not parsed.path.strip("/"):
        raise RuntimeError(f"更新包 URL 不完整：{url}")
    if parsed.scheme == "http" and (parsed.hostname or "") not in LOOPBACK:
        raise RuntimeError(
            f"http 端点只允许本机联调：{url}；正式部署请改用 https（并关闭 "
            "dangerousInsecureTransportProtocol）"
        )
    if not parsed.path.rstrip("/").endswith(setup_name):
        raise RuntimeError(f"URL 指向的文件名与安装包不一致：{url} != {setup_name}")


def expected_manifest(version: str, signature: str, url: str, notes: str) -> dict[str, object]:
    return {
        "version": version,
        "notes": notes,
        "pub_date": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "platforms": {PLATFORM: {"signature": signature, "url": url}},
    }


def build(args: argparse.Namespace) -> int:
    version = sync_version.read_version()
    setup = Path(args.setup).resolve() if args.setup else NSIS_DIR / f"Plateau_{version}_x64-setup.exe"
    if not setup.is_file():
        raise RuntimeError(f"安装包不存在：{setup}")
    if version not in setup.name:
        raise RuntimeError(f"安装包 {setup.name} 与 VERSION {version} 不匹配，拒绝发布错版本")
    sig_path = Path(args.sig).resolve() if args.sig else Path(str(setup) + ".sig")
    signature = read_signature(sig_path)

    url = args.url or f"{args.base_url.rstrip('/')}/{setup.name}"
    validate_url(url, setup.name)

    out = Path(args.out).resolve() if args.out else setup.parent / "latest.json"
    notes = args.notes or f"Plateau Dashboard {version}"
    doc = expected_manifest(version, signature, url, notes)
    _ = out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] 生成 {out}")
    print(f"     version={version} platform={PLATFORM} url={url}")
    print(f"     本地服务器：python -m http.server 17865 --bind 127.0.0.1 --directory {out.parent}")
    return 0


def verify(args: argparse.Namespace) -> int:
    version = sync_version.read_version()
    setup = Path(args.setup).resolve() if args.setup else NSIS_DIR / f"Plateau_{version}_x64-setup.exe"
    out = Path(args.out).resolve() if args.out else setup.parent / "latest.json"
    if not out.is_file():
        raise RuntimeError(f"manifest 不存在：{out}")
    doc = json.loads(out.read_text(encoding="utf-8"))
    problems: list[str] = []
    if doc.get("version") != version:
        problems.append(f"version={doc.get('version')} != VERSION {version}")
    platform = (doc.get("platforms") or {}).get(PLATFORM) or {}
    sig_path = Path(args.sig).resolve() if args.sig else Path(str(setup) + ".sig")
    if sig_path.is_file():
        if platform.get("signature") != read_signature(sig_path):
            problems.append("platforms.windows-x86_64.signature 与 .sig 文件不一致")
    if not platform.get("url"):
        problems.append("platforms.windows-x86_64.url 缺失")
    else:
        validate_url(str(platform["url"]), setup.name)
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    print(f"[OK] manifest 与 VERSION / .sig / URL 一致：{out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 / 校验 Tauri updater static manifest")
    _ = parser.add_argument("--setup", help="NSIS 安装包路径")
    _ = parser.add_argument("--sig", help="签名文件路径，默认 <setup>.sig")
    _ = parser.add_argument("--url", help="安装包完整下载 URL，优先于 --base-url")
    _ = parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="下载根 URL")
    _ = parser.add_argument("--notes", help="更新说明")
    _ = parser.add_argument("--out", help="latest.json 输出路径")
    _ = parser.add_argument("--verify", action="store_true", help="只校验不写入")
    args = parser.parse_args()
    try:
        return verify(args) if bool(args.verify) else build(args)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
