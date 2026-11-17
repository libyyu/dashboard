# -*- coding: utf-8 -*-
"""基建日志的目录 / 按日命名 / 保留策略单一真源（stdlib only）。

桌面包 launcher 注入 ``PLATEAU_LOGS_DIR`` 作为权威目录；未注入时按与
ports.home_dir()/profile_id() 相同的规则回退，保证 launcher 与 backend
写到同一个地方。按日命名 + 保留天数借鉴 EvoFlow 的思路，自行实现，
不依赖任何三方库，也不 import app 包（launcher 会按文件路径直接加载）。
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

DEFAULT_RETENTION_DAYS = 14
MAX_RETENTION_DAYS = 90
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MIN_MAX_BYTES = 1024 * 1024
MAX_MAX_BYTES = 64 * 1024 * 1024

# {prefix}-{YYYY-MM-DD}.log 或 {prefix}-{YYYY-MM-DD}.{part}.log
_DAILY_RE = re.compile(
    r"^(?P<prefix>[A-Za-z0-9_.-]+)-(?P<day>\d{4}-\d{2}-\d{2})(?:\.(?P<part>\d+))?\.log$"
)
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def env_flag(name: str, default: bool = False) -> bool:
    """显式开关：只认 1/true/yes/on 与 0/false/no/off，未设置或非法值用默认。"""
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def retention_days() -> int:
    raw = (os.environ.get("PLATEAU_LOG_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        return max(1, min(MAX_RETENTION_DAYS, int(raw)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def max_bytes() -> int:
    """单个日志分片的软上限，超过后写下一个分片（不是删日志）。"""
    raw = (os.environ.get("PLATEAU_LOG_MAX_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        return max(MIN_MAX_BYTES, min(MAX_MAX_BYTES, int(raw)))
    except ValueError:
        return DEFAULT_MAX_BYTES


def profile_id() -> str:
    return (os.environ.get("PLATEAU_PROFILE") or "default").strip() or "default"


def home_dir() -> Path:
    raw = (os.environ.get("PLATEAU_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".plateau-dashboard").resolve()


def logs_dir() -> Path:
    """权威目录：PLATEAU_LOGS_DIR > PLATEAU_HOME/profiles/<profile>/logs。"""
    raw = (os.environ.get("PLATEAU_LOGS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (home_dir() / "profiles" / profile_id() / "logs").resolve()


def daily_log_name(prefix: str, day: date | None = None, part: int = 0) -> str:
    safe = _UNSAFE_NAME_RE.sub("_", prefix).strip("._") or "log"
    stamp = (day or date.today()).isoformat()
    return f"{safe}-{stamp}.log" if part <= 0 else f"{safe}-{stamp}.{part}.log"


def daily_log_path(
    prefix: str, day: date | None = None, part: int = 0, root: Path | None = None
) -> Path:
    return (root or logs_dir()) / daily_log_name(prefix, day, part)


def parse_daily_name(name: str) -> tuple[str, date, int] | None:
    match = _DAILY_RE.match(Path(name).name)
    if match is None:
        return None
    try:
        parsed_day = date.fromisoformat(match.group("day"))
    except ValueError:
        return None
    return match.group("prefix"), parsed_day, int(match.group("part") or 0)


def is_safe_log_name(name: str) -> bool:
    """日志消费接口的路径守卫：只允许 logs_dir 下的按日文件名，防穿越。"""
    return Path(name).name == name and parse_daily_name(name) is not None


def list_daily_logs(root: Path | None = None) -> list[Path]:
    directory = root or logs_dir()
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir()
         if path.is_file() and parse_daily_name(path.name) is not None),
        key=lambda path: path.name,
    )


def prune_daily_logs(root: Path | None = None, days: int | None = None) -> int:
    """按文件名里的日期删除过期日志；只删本模块命名的文件，不碰其它文件。"""
    directory = root or logs_dir()
    keep = days if days is not None else retention_days()
    cutoff = date.today() - timedelta(days=keep)
    removed = 0
    for path in list_daily_logs(directory):
        parsed = parse_daily_name(path.name)
        if parsed is None or parsed[1] >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
