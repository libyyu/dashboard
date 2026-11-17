# -*- coding: utf-8 -*-
"""版本化 DDL 迁移：所有 CREATE TABLE 集中在这里，业务层不写方言 SQL。

不引入 Alembic / SQLAlchemy：桌面包是 PyInstaller 冻结的离线单机应用，
零外部依赖可避免包体膨胀与 hiddenimport 复杂度；后续若换 PostgreSQL，
只需替换本模块的迁移实现，core 的 Storage 端口与业务代码不动。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# (版本号, 名称, DDL)。每个版本只执行一次，结果记在 schema_migrations。
MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "init_kv_store",
        """
        CREATE TABLE IF NOT EXISTS plateau_kv (
            collection TEXT NOT NULL,
            key        TEXT NOT NULL,
            payload    TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (collection, key)
        )
        """,
    ),
)

_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply(conn: sqlite3.Connection) -> int:
    """幂等应用所有未执行的迁移，返回当前 schema 版本。可安全重复调用。"""
    _ = conn.execute(_SCHEMA_TABLE)
    conn.commit()
    applied = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, name, ddl in MIGRATIONS:
        if version in applied:
            continue
        with conn:
            for statement in (part.strip() for part in ddl.split(";") if part.strip()):
                _ = conn.execute(statement)
            _ = conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, _utc_now()),
            )
        applied.add(version)
    return max(applied, default=0)


def latest_version() -> int:
    return max((version for version, _, _ in MIGRATIONS), default=0)
