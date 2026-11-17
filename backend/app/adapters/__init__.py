# -*- coding: utf-8 -*-
"""适配器层：core 端口的具体实现，依赖方向 adapters -> core。

这里不做 eager import，避免子模块之间形成 import 环；调用方直接
``from app.adapters.sqlite_storage import SqliteStorage`` 即可。
"""
from __future__ import annotations

__all__: list[str] = []
