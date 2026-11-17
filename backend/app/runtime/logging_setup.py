# -*- coding: utf-8 -*-
"""结构化 JSONL 日志装配（stdlib only）。

- 按日 + 大小上限分片，append 写入，跨日自动切文件（不依赖 RotatingFileHandler，
  Windows 多进程轮转不可靠，这里每个进程只写自己的 prefix）
- JSON lines：ts / level / logger / service / pid / profile / message + 任意 extra 字段
- 统一脱敏：Bearer / ck_ / JWT / 敏感 k=v / 敏感 header 名
- 接管 root 与 uvicorn logger 树；run.py 用 log_config=None 关掉 uvicorn 的 dictConfig，
  否则它会重置 root handlers
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import TextIO, cast

PathFactory = Callable[[str, date, int], Path]

_CREDENTIAL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+"), r"\1[REDACTED]"),
    (re.compile(r"\bck_[A-Za-z0-9_\-]{6,}\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{8,}\b"), "[REDACTED]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|authorization|cookie)"
            r"(\s*[=:]\s*)([^\s,;&\"']+)"
        ),
        r"\1\2[REDACTED]",
    ),
)
_SENSITIVE_HEADER_RE = re.compile(
    r"(?i)(authorization|proxy-authorization|cookie|set-cookie|token|secret|password|"
    r"api[_-]?key|access[_-]?key|private[_-]?key|x-codebuddy-api-key)"
)
_STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}

_configured_service = ""


def redact_text(value: str) -> str:
    """兜底脱敏：任何日志内容（message / extra / stack）都过这一层。"""
    text = value
    for pattern, replacement in _CREDENTIAL_RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """敏感 header 整项置 [REDACTED]，其余值走兜底脱敏。"""
    return {
        key: ("[REDACTED]" if _SENSITIVE_HEADER_RE.search(key) else redact_text(value))
        for key, value in headers.items()
    }


def _jsonable(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return redact_text(str(value))


class JsonLineFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, timezone.utc)
        payload: dict[str, object] = {
            "ts": created.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "pid": os.getpid(),
            "profile": (os.environ.get("PLATEAU_PROFILE") or "default"),
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = _jsonable(value)
        if record.exc_info is not None:
            exc_info = cast(
                tuple[type[BaseException], BaseException, TracebackType | None],
                record.exc_info,
            )
            payload["error_type"] = exc_info[0].__name__
            stack = "".join(traceback.format_exception(*exc_info))
            payload["stack"] = redact_text(stack)[-2000:]
        return json.dumps(payload, ensure_ascii=False, default=str)


class DailyJsonFileHandler(logging.Handler):
    """按日命名 + 大小上限分片的 append handler；每个进程只写自己的 prefix。"""

    def __init__(self, prefix: str, service: str, path_factory: PathFactory,
                 max_file_bytes: int) -> None:
        super().__init__()
        self._prefix = prefix
        self._path_factory = path_factory
        self._max_file_bytes = max_file_bytes
        self._day: date | None = None
        self._part = 0
        self._file: TextIO | None = None
        self.setFormatter(JsonLineFormatter(service))

    def _open(self, day: date, part: int) -> None:
        self._close_file()
        path = self._path_factory(self._prefix, day, part)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8", errors="replace")
        self._day = day
        self._part = part

    def _close_file(self) -> None:
        if self._file is not None:
            with contextlib.suppress(Exception):
                self._file.flush()
                self._file.close()
            self._file = None

    def _ensure_file(self) -> TextIO:
        today = date.today()
        if self._file is None or self._day != today:
            part = 0
            while part < 100:
                path = self._path_factory(self._prefix, today, part)
                if not path.is_file() or path.stat().st_size < self._max_file_bytes:
                    break
                part += 1
            self._open(today, part)
        elif self._file.tell() >= self._max_file_bytes:
            self._open(today, self._part + 1)
        assert self._file is not None
        return self._file

    def emit(self, record: logging.LogRecord) -> None:
        self.acquire()
        try:
            handle = self._ensure_file()
            handle.write(self.format(record) + "\n")
            handle.flush()
        except Exception:  # noqa: BLE001 - 日志失败绝不影响业务
            self.handleError(record)
        finally:
            self.release()

    def close(self) -> None:
        self.acquire()
        try:
            self._close_file()
        finally:
            self.release()
        super().close()


def configure_logging(
    service: str,
    log_dir: Path,
    path_factory: PathFactory,
    *,
    console: bool = True,
    level: int = logging.INFO,
    max_file_bytes: int = 0,
) -> Path:
    """装配 root + uvicorn logger 树。幂等；同一进程重复调用不会叠加 handler。"""
    global _configured_service
    log_dir.mkdir(parents=True, exist_ok=True)
    if _configured_service == service:
        return log_dir

    limit = max_file_bytes if max_file_bytes > 0 else 8 * 1024 * 1024
    file_handler = DailyJsonFileHandler(service, service, path_factory, limit)
    file_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [file_handler]
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(level)
        stream.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root.handlers.append(stream)

    # uvicorn 默认 dictConfig 会给这些 logger 挂自己的 handler 且 propagate=False；
    # 这里统一交给 root，access log 由 RequestLogMiddleware 替代。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
    logging.captureWarnings(True)
    _configured_service = service
    return log_dir


def shutdown_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        with contextlib.suppress(Exception):
            handler.flush()
            handler.close()
