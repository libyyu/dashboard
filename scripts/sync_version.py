# -*- coding: utf-8 -*-
"""单一版本源 VERSION → 各组件回写 + 全量一致性检查。

用法：
  python scripts/sync_version.py             # 检查七处一致性（CI 用），不一致退出 1
  python scripts/sync_version.py --update   # 把 VERSION 回写到其余六处

借鉴 EvoFlow 的 evopanel/VERSION + sync-version.js：多服务打一个包时，
避免「前端 1.0.0 / 后端 0.9.9 / 安装包 1.0.1」这类版本错配。
ci_cheap.py 直接调用 version_problems()，不重复维护一份版本号清单。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# VERSION 之外的六处派生版本（相对 ROOT）
_JSON_TARGETS = (
    "frontend/package.json",
    "desktop/services.json",
    "desktop/package.json",
    "desktop/src-tauri/tauri.conf.json",
    "contracts/api.json",
)
_CARGO_TOML = "desktop/src-tauri/Cargo.toml"

# 只匹配 [package] 段内的第一个 version，避免误伤依赖里的 version = "2"
_CARGO_PACKAGE_VERSION = re.compile(
    r'(?ms)^(\[package\][\s\S]*?^version\s*=\s*")([^"]+)(")'
)


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def read_services_doc() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((ROOT / "desktop" / "services.json").read_text(encoding="utf-8")),
    )


def _read_json(rel: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((ROOT / rel).read_text(encoding="utf-8")))


def _read_cargo_version() -> str:
    match = _CARGO_PACKAGE_VERSION.search((ROOT / _CARGO_TOML).read_text(encoding="utf-8"))
    return match.group(2) if match else ""


def component_versions() -> dict[str, str]:
    """返回各组件当前版本号（不含 VERSION 本身）。"""
    versions = {rel: str(_read_json(rel).get("version", "")) for rel in _JSON_TARGETS}
    versions[_CARGO_TOML] = _read_cargo_version()
    return versions


def version_problems() -> list[str]:
    """VERSION 与六处派生版本不一致的问题列表；空列表代表一致。"""
    version = read_version()
    return [
        f"{rel} version={found or '<缺失>'} != VERSION {version}"
        for rel, found in component_versions().items()
        if found != version
    ]


def _write_json(rel: str, version: str) -> None:
    path = ROOT / rel
    data = _read_json(rel)
    data["version"] = version
    _ = path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_cargo(version: str) -> None:
    path = ROOT / _CARGO_TOML
    text = path.read_text(encoding="utf-8")
    new_text, count = _CARGO_PACKAGE_VERSION.subn(
        lambda m: m.group(1) + version + m.group(3), text, count=1
    )
    if count != 1:
        raise RuntimeError(f"{_CARGO_TOML} 未找到 [package] version")
    _ = path.write_text(new_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="版本一致性同步")
    _ = parser.add_argument("--update", action="store_true", help="回写版本文件而不是仅检查")
    args = parser.parse_args()
    update = cast(bool, args.update)

    version = read_version()
    services_doc = read_services_doc()
    raw_services = cast(list[dict[str, object]], services_doc.get("services", []))
    services = [str(s["name"]) for s in raw_services]

    if update:
        if services_doc.get("product") != "plateau-dashboard":
            services_doc["product"] = "plateau-dashboard"
            _ = (ROOT / "desktop" / "services.json").write_text(
                json.dumps(services_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print("[OK] 回写 desktop/services.json: product=plateau-dashboard")
        for rel, found in component_versions().items():
            if found == version:
                continue
            if rel == _CARGO_TOML:
                _write_cargo(version)
            else:
                _write_json(rel, version)
            print(f"[OK] 回写 {rel}: {found or '<缺失>'} -> {version}")
        return 0

    problems = version_problems()
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print("[FAIL] 版本不一致，运行 python scripts/sync_version.py --update")
        return 1
    print(f"[OK] 七处版本一致: {version} (services={services})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
