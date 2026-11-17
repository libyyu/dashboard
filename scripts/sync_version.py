# -*- coding: utf-8 -*-
"""单一版本源 VERSION → 各组件回写 + 包内清单版本同步。

用法：
  python scripts/sync_version.py             # 检查一致性（CI 用），不一致退出 1
  python scripts/sync_version.py --update   # 回写 package.json 与 desktop/services.json 的 version

借鉴 EvoFlow 的 evopanel/VERSION + sync-version.js：多服务打一个包时，
避免「前端 1.0.0 / 后端 0.9.9」这类版本错配。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
SERVICES_JSON = ROOT / "desktop" / "services.json"


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def read_services_doc() -> dict[str, object]:
    return cast(dict[str, object], json.loads(SERVICES_JSON.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description="版本一致性同步")
    _ = parser.add_argument("--update", action="store_true", help="回写版本文件而不是仅检查")
    args = parser.parse_args()
    update = cast(bool, args.update)

    version = read_version()
    package = cast(dict[str, object], json.loads(PACKAGE_JSON.read_text(encoding="utf-8")))
    services_doc = read_services_doc()
    raw_services = cast(list[dict[str, object]], services_doc.get("services", []))
    services = [str(s["name"]) for s in raw_services]

    package_version = str(package.get("version", ""))
    services_version = str(services_doc.get("version", ""))

    if update:
        if package_version != version:
            package["version"] = version
            _ = PACKAGE_JSON.write_text(
                json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"[OK] 回写 {PACKAGE_JSON.relative_to(ROOT)}: {package_version} -> {version}")
        if services_doc.get("product") != "plateau-dashboard" or services_version != version:
            services_doc["product"] = "plateau-dashboard"
            services_doc["version"] = version
            _ = SERVICES_JSON.write_text(
                json.dumps(services_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"[OK] 回写 {SERVICES_JSON.relative_to(ROOT)}: version={services_version} -> {version}")
        return 0

    problems: list[str] = []
    if package_version != version:
        problems.append(f"frontend/package.json version={package_version} != VERSION {version}")
    if services_version != version:
        problems.append(f"desktop/services.json version={services_version or '<缺失>'} != VERSION {version}")
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print("[FAIL] 版本不一致，运行 python scripts/sync_version.py --update")
        return 1
    print(f"[OK] 版本一致: {version} (services={services})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
