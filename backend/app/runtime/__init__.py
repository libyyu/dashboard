# -*- coding: utf-8 -*-
"""桌面包运行时底座：端口分配、分层探活、三车道可用性隔离。

与任何具体业务模块解耦。
"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """资源基准目录（VERSION / frontend/dist 等打包数据）。

    源码态 = 项目根 plateau-dashboard/；冻结态 = sys._MEIPASS（PyInstaller
    --add-data 打入的数据目录）。
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def package_version() -> str:
    """单一版本源：资源目录下 VERSION 文件。"""
    path = resource_dir() / "VERSION"
    try:
        return path.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


__all__ = ["package_version", "resource_dir"]
