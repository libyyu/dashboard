# -*- coding: utf-8 -*-
"""只读日志消费入口：列出日志文件 / 读尾部（本机排障用）。

刻意不提供「下载全部」「任意路径读取」：source 必须是日志目录下符合
{prefix}-YYYY-MM-DD[.N].log 命名的文件名，读取上限 1MB / 500 行，
所有返回值再过一次脱敏。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, cast

from fastapi import FastAPI, Query
from starlette.responses import JSONResponse

from app.core.errors import error_response

from . import log_policy
from .logging_setup import redact_text

_MAX_TAIL_BYTES = 1024 * 1024
_MAX_LINE_CHARS = 4000


def _sanitize(entry: dict[str, object]) -> dict[str, object]:
    return {
        str(key): (redact_text(value)[:_MAX_LINE_CHARS] if isinstance(value, str) else value)
        for key, value in entry.items()
    }


def _read_tail(path: Path) -> tuple[list[dict[str, object]], bool]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - _MAX_TAIL_BYTES)
        handle.seek(start)
        raw = handle.read()
    entries: list[dict[str, object]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(_sanitize(cast(dict[str, object], json.loads(line))))
        except ValueError:
            entries.append({"level": "RAW", "message": redact_text(line)[:_MAX_LINE_CHARS]})
    return entries, start > 0


def register_log_routes(app: FastAPI) -> None:
    @app.get("/api/logs/sources", tags=["logs"])
    def sources() -> dict[str, object]:
        items: list[dict[str, object]] = []
        for path in log_policy.list_daily_logs():
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({
                "name": path.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                .isoformat(timespec="seconds"),
            })
        return {
            "root": str(log_policy.logs_dir()),
            "retention_days": log_policy.retention_days(),
            "sources": items,
        }

    @app.get("/api/logs/tail", tags=["logs"], response_model=None)
    def tail(
        source: Annotated[str, Query(description="日志文件名，留空取当天 dashboard")] = "",
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        level: Annotated[str, Query(description="按 level 过滤，留空不过滤")] = "",
        request_id: Annotated[str, Query(description="按 request_id 下钻，留空不过滤")] = "",
    ) -> dict[str, object] | JSONResponse:
        name = source or log_policy.daily_log_name("dashboard")
        if not log_policy.is_safe_log_name(name):
            return error_response("INVALID_SOURCE", 400, "invalid source")
        path = log_policy.logs_dir() / name
        if not path.is_file():
            return {"source": name, "exists": False, "lines": [], "truncated": False}
        entries, truncated = _read_tail(path)
        if level:
            wanted = level.upper()
            entries = [e for e in entries if str(e.get("level", "")).upper() == wanted]
        if request_id:
            entries = [e for e in entries if str(e.get("request_id", "")) == request_id]
        return {
            "source": name,
            "exists": True,
            "truncated": truncated,
            "lines": entries[-limit:],
        }
