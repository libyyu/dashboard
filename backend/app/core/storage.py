# -*- coding: utf-8 -*-
"""可移植存储端口：业务层只见 Protocol，不见 SQL / 驱动 / 方言。

后期先接 SQLite，之后可换 PostgreSQL 等，切换只换 adapter，不动业务代码。

可移植约束（适配器必须遵守，cheap CI 静态守卫）：
1. 主键用 UUID 字符串，不依赖行号伪列或整数自增列
2. 时间统一 UTC aware datetime，写库前转换，不用数据库的当前时间方言
3. JSON 字段走 set_json / get_json 抽象，由适配器决定列类型
4. 不用方言专有的 upsert、批量插入语法或专属配置指令；冲突处理在业务层表达
5. DDL 与迁移交给 Alembic，业务代码不写 CREATE TABLE
6. 连接串只来自 DATABASE_URL，驱动由 adapter 决定，业务层不 import 驱动
"""
from __future__ import annotations

import os
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

SUPPORTED_DIALECTS = ("sqlite", "postgresql", "mysql")


class Storage(Protocol):
    """集合式仓储端口；集合名由领域定义，适配器映射到表/文档。"""

    def get(self, collection: str, key: str) -> dict[str, object] | None: ...
    def put(self, collection: str, key: str, value: dict[str, object]) -> None: ...
    def delete(self, collection: str, key: str) -> bool: ...
    def query(
        self,
        collection: str,
        *,
        filters: dict[str, object] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]: ...
    def set_json(self, collection: str, key: str, field: str,
                 value: dict[str, object] | list[object]) -> None: ...
    def transaction(self) -> AbstractContextManager[object]: ...


@dataclass(frozen=True)
class DatabaseConfig:
    """唯一连接配置来源：DATABASE_URL（如 sqlite:///... 或 postgresql+psycopg://...）。"""

    url: str
    enabled: bool

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        raw = (os.environ.get("DATABASE_URL") or "").strip()
        return cls(url=raw, enabled=bool(raw))

    @property
    def dialect(self) -> str:
        if not self.url:
            return "unconfigured"
        return self.url.split(":", 1)[0].split("+", 1)[0].lower()

    @property
    def supported(self) -> bool:
        return self.dialect in SUPPORTED_DIALECTS


class NotConfiguredStorage:
    """未启用数据库时的空实现：让业务代码在迁移前后都不用改调用点。"""

    def get(self, collection: str, key: str) -> dict[str, object] | None:
        _ = (collection, key)
        return None

    def put(self, collection: str, key: str, value: dict[str, object]) -> None:
        _ = (collection, key, value)
        return None

    def delete(self, collection: str, key: str) -> bool:
        _ = (collection, key)
        return False

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, object] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _ = (collection, filters, limit, offset)
        return []

    def set_json(self, collection: str, key: str, field: str,
                 value: dict[str, object] | list[object]) -> None:
        _ = (collection, key, field, value)
        return None

    def transaction(self) -> AbstractContextManager[object]:
        from contextlib import nullcontext
        return nullcontext(None)
