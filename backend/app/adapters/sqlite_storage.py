# -*- coding: utf-8 -*-
"""SQLite 适配器：实现 core.storage.Storage 端口，只用 stdlib。

可移植约束落实：
- 主键是业务传入的 UUID/字符串 key，不用自增列或 rowid
- 时间由 Python 生成 UTC aware ISO 字符串，不用数据库 CURRENT_TIMESTAMP
- JSON 走 payload 列 + json 模块，业务层只看到 dict
- 冲突处理用「先删后插」，不用 ON CONFLICT / INSERT OR REPLACE 等方言 upsert
- 连接串只来自 DATABASE_URL，DDL 全部在 migrations.py
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, cast

from app.adapters import migrations
from app.core.storage import DatabaseConfig

_SQLITE_PREFIX = "sqlite:///"

# query() 单次最多扫描的行数，防止无过滤条件时把全表拉进内存
_SCAN_CAP = 10_000


def parse_sqlite_url(url: str) -> Path:
    """sqlite:///<绝对路径> → Path；拒绝内存库与相对路径。"""
    if not url.startswith(_SQLITE_PREFIX):
        raise ValueError(f"仅支持 {_SQLITE_PREFIX}<绝对路径>：{url}")
    raw = url[len(_SQLITE_PREFIX):]
    if not raw or raw.startswith(":memory:"):
        raise ValueError(f"数据库路径不能为空或内存库：{url}")
    return Path(raw)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteStorage:
    """集合式 KV 仓储；线程安全（FastAPI 通过 asyncio.to_thread 调用）。"""

    def __init__(self, config: DatabaseConfig) -> None:
        if config.dialect != "sqlite" or not config.enabled:
            raise ValueError(f"SqliteStorage 需要已启用的 sqlite 配置：{config.dialect}")
        self.path = parse_sqlite_url(config.url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.schema_version = migrations.apply(self._conn)

    def _read(self, collection: str, key: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT payload FROM plateau_kv WHERE collection = ? AND key = ?",
            (collection, key),
        ).fetchone()
        if row is None:
            return None
        parsed = json.loads(str(row["payload"]))
        return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None

    def _write(self, collection: str, key: str, value: dict[str, object]) -> None:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        # 先删后插：不依赖任何方言 upsert 语法
        _ = self._conn.execute(
            "DELETE FROM plateau_kv WHERE collection = ? AND key = ?", (collection, key)
        )
        _ = self._conn.execute(
            "INSERT INTO plateau_kv (collection, key, payload, updated_at) VALUES (?, ?, ?, ?)",
            (collection, key, payload, _utc_now()),
        )

    def get(self, collection: str, key: str) -> dict[str, object] | None:
        with self._lock:
            return self._read(collection, key)

    def put(self, collection: str, key: str, value: dict[str, object]) -> None:
        with self._lock, self._conn:
            self._write(collection, key, value)

    def delete(self, collection: str, key: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM plateau_kv WHERE collection = ? AND key = ?", (collection, key)
            )
            return cursor.rowcount > 0

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, object] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        # 过滤在 Python 侧表达，不生成 json_extract 之类的方言 SQL
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, payload FROM plateau_kv WHERE collection = ? LIMIT ?",
                (collection, _SCAN_CAP),
            ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            parsed = json.loads(str(row["payload"]))
            if not isinstance(parsed, dict):
                continue
            item = cast(dict[str, object], parsed)
            if filters and any(item.get(name) != expected for name, expected in filters.items()):
                continue
            items.append({"key": str(row["key"]), **item})
        return items[offset: offset + max(0, limit)]

    def set_json(self, collection: str, key: str, field: str,
                 value: dict[str, object] | list[object]) -> None:
        with self._lock, self._conn:
            record = self._read(collection, key) or {}
            record[field] = value
            self._write(collection, key, record)

    @contextmanager
    def transaction(self) -> Iterator[object]:
        with self._lock, self._conn:
            yield self

    def health(self) -> dict[str, object]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT collection, COUNT(*) AS n FROM plateau_kv GROUP BY collection"
            ).fetchall()
            counts = {str(row["collection"]): int(row["n"]) for row in rows}
        return {
            "dialect": "sqlite",
            "enabled": True,
            "schema_version": self.schema_version,
            "latest_migration": migrations.latest_version(),
            "path": str(self.path),
            "collections": counts,
        }
