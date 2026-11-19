# -*- coding: utf-8 -*-
"""SQLite 适配器的进程级懒装配（服务定位）。

routers 多处（runtime / model / agent / plugins）需要同一个 SqliteStorage 实例，
集中在这里管理单例，避免：
- model ↔ runtime 循环导入（_get_storage 原先在 runtime.py，model.py 又 import 它）
- 各 router 各持一份单例，导致同一 DB 打开多个连接
"""
from __future__ import annotations

import threading

from app.adapters.sqlite_storage import SqliteStorage
from app.core.storage import DatabaseConfig

_storage: SqliteStorage | None = None
_storage_lock = threading.Lock()


def get_storage() -> SqliteStorage | None:
    """按 DATABASE_URL 懒装配 SQLite 适配器；未配置返回 None（保持可运行性）。"""
    global _storage
    config = DatabaseConfig.from_env()
    if not config.enabled:
        return None
    if config.dialect != "sqlite":
        raise ValueError(f"当前只装配了 sqlite 适配器：{config.dialect}")
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                _storage = SqliteStorage(config)
    return _storage
