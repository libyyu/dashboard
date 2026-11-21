# -*- coding: utf-8 -*-
"""升级版本并记录更新内容。

仓库约定：每次代码改动都升版本号，并在 CHANGELOG.md 留下更新内容。
本脚本负责：算新版本 → 写 VERSION → 回写所有派生点 → 追加/校验 CHANGELOG 条目 → 复查一致性。

用法：
  python scripts/bump_version.py --notes "修复 xxx"                  # patch +1
  python scripts/bump_version.py --minor --notes "新增 xxx"
  python scripts/bump_version.py --version 1.2.0 --notes "..."      # 显式指定
  python scripts/bump_version.py --notes-file notes.md               # 长条目
  python scripts/bump_version.py --check                             # CI 守卫，不写文件
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_version  # noqa: E402

VERSION_FILE = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _decode(raw: bytes) -> str:
    """Windows 下子进程 stdout 可能是系统 ANSI（GBK），不能用 text=True + utf-8 硬解。"""
    for encoding in ("utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def has_entry(version: str) -> bool:
    if not CHANGELOG.is_file():
        return False
    return re.search(rf"(?m)^## \[{re.escape(version)}\]", CHANGELOG.read_text(encoding="utf-8")) is not None


def append_entry(version: str, notes: str) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"(?m)^## \[", text)
    if match is None:
        raise RuntimeError("CHANGELOG.md 缺少版本条目锚点 '## ['")
    bullets = "\n".join(
        f"- {line.lstrip('-').strip()}"
        for line in notes.splitlines()
        if line.strip()
    )
    entry = f"## [{version}] - {date.today().isoformat()}\n\n### 变更\n\n{bullets}\n\n"
    _ = CHANGELOG.write_text(text[:match.start()] + entry + text[match.start():], encoding="utf-8")


def _sync_registry_changelog() -> None:
    """升版本后同步 registry 的 data/changelog.json（单一真源 = dashboard CHANGELOG.md）。

    registry 默认与 dashboard 同父目录（../plateau-registry），可用 PLATEAU_REGISTRY_REPO 覆盖。
    找不到脚本或同步失败只告警、不阻断升版本：registry 可能未检出或离线。
    """
    registry_repo = Path(os.environ.get("PLATEAU_REGISTRY_REPO") or ROOT.parent / "plateau-registry")
    sync_script = registry_repo / "sync_changelog.py"
    if not sync_script.is_file():
        print(f"[warn] 未找到 registry 同步脚本 {sync_script}（可设 PLATEAU_REGISTRY_REPO），跳过 changelog 同步")
        return
    proc = subprocess.run(
        [sys.executable, str(sync_script)],
        cwd=str(registry_repo), capture_output=True,
    )
    if proc.stdout:
        print(_decode(proc.stdout).strip())
    if proc.returncode != 0:
        if proc.stderr:
            print(_decode(proc.stderr).strip())
        print("[warn] registry changelog 同步失败，已跳过（不影响升版本）")


def check() -> int:
    problems = sync_version.version_problems()
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not has_entry(version):
        problems.append(f"CHANGELOG.md 缺少 [{version}] 条目")
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    print(f"[OK] 版本 {version} 全处一致，CHANGELOG 已记录")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="升级版本并记录更新内容")
    _ = parser.add_argument("--version", help="显式指定新版本 X.Y.Z")
    _ = parser.add_argument("--major", action="store_true", help="主版本 +1")
    _ = parser.add_argument("--minor", action="store_true", help="次版本 +1")
    _ = parser.add_argument("--notes", help="本次更新内容（可多行）")
    _ = parser.add_argument("--notes-file", help="从 UTF-8 文件读取更新内容")
    _ = parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()

    if bool(args.check):
        return check()

    current = VERSION_FILE.read_text(encoding="utf-8").strip()
    parsed = SEMVER.match(current)
    if parsed is None:
        print(f"[FAIL] VERSION 不是 X.Y.Z：{current}")
        return 1

    if args.version:
        target = str(args.version).strip()
        if SEMVER.match(target) is None:
            print(f"[FAIL] 目标版本不是 X.Y.Z：{target}")
            return 1
    else:
        major, minor, patch = (int(part) for part in parsed.groups())
        if bool(args.major):
            major, minor, patch = major + 1, 0, 0
        elif bool(args.minor):
            minor, patch = minor + 1, 0
        else:
            patch += 1
        target = f"{major}.{minor}.{patch}"

    if target == current:
        print(f"[FAIL] 新版本与当前版本相同：{target}")
        return 1

    notes = str(args.notes or "")
    if args.notes_file:
        path = Path(str(args.notes_file))
        if not path.is_absolute():
            path = ROOT / path
        notes = path.read_text(encoding="utf-8")
    notes = notes.strip()

    # 允许先手写详细 CHANGELOG 条目再跑 bump；否则必须有 --notes。
    if not notes and not has_entry(target):
        print(f"[FAIL] 需要 --notes / --notes-file，或先在 CHANGELOG.md 写好 [{target}] 条目")
        return 1

    _ = VERSION_FILE.write_text(target + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_version.py"), "--update"],
        cwd=str(ROOT), capture_output=True,
    )
    if proc.stdout:
        print(_decode(proc.stdout).strip())
    if proc.returncode != 0:
        print(_decode(proc.stderr).strip())
        return int(proc.returncode)

    if notes and not has_entry(target):
        append_entry(target, notes)
    _sync_registry_changelog()
    print(f"[OK] {current} → {target}，更新内容已记录")
    return check()


if __name__ == "__main__":
    sys.exit(main())
