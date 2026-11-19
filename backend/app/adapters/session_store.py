# -*- coding: utf-8 -*-
"""SessionStore 的 SQLite 实现：复用 core.storage.Storage 端口，零新增 DDL。

事件持久化复用 plateau_kv：
- collection ``agent_sessions``：key=session_id，payload 含 last_seq / title / 时间
- collection ``agent_events``：key=``{session_id}:{seq:08d}``，payload 是事件全字段

seq 由本适配器单调分配（append 内加锁），SSE 帧用 ``id:{seq}`` 供断点续读
（Last-Event-ID）。单机桌面应用事件量远低于 Storage 的扫描上限，按 key 排序即可
保证回放顺序，不必为事件流单独建表、引入方言索引。
"""
from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import cast

from app.core.agent import AgentEvent
from app.core.storage import Storage

_SESSIONS = "agent_sessions"
_EVENTS = "agent_events"
_MESSAGES = "agent_messages"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_or(value: object, default: int = -1) -> int:
    """读取整数；0 是合法值，不能用 `or -1`（0 会被当成缺失而丢帧/卡在 0）。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _event_to_record(event: AgentEvent, seq: int) -> dict[str, object]:
    return {
        "type": event.type,
        "seq": seq,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "trace_id": event.trace_id,
        "request_id": event.request_id,
        "payload": event.payload,
        "created_at": _utc_now(),
    }


def _record_to_event(record: dict[str, object]) -> AgentEvent:
    payload = record.get("payload")
    return AgentEvent(
        type=str(record.get("type") or ""),
        run_id=str(record.get("run_id") or ""),
        session_id=str(record.get("session_id") or ""),
        seq=_int_or(record.get("seq")),
        trace_id=str(record.get("trace_id") or ""),
        request_id=str(record.get("request_id") or ""),
        payload=cast(dict[str, object], payload) if isinstance(payload, dict) else {},
    )


class SqliteSessionStore:
    """线程安全的会话事件仓储；所有写操作串行化保证 seq 单调。"""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = threading.Lock()

    def _ensure_session_unlocked(self, session_id: str, title: str = "") -> None:
        """调用方必须已持有 self._lock；threading.Lock 不可重入，锁内不得再调加锁方法。"""
        if self._storage.get(_SESSIONS, session_id) is not None:
            return
        self._storage.put(_SESSIONS, session_id, {
            "title": title,
            "last_seq": -1,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "archived": False,
        })

    def ensure_session(self, session_id: str, title: str = "") -> None:
        with self._lock:
            self._ensure_session_unlocked(session_id, title)

    def session_exists(self, session_id: str) -> bool:
        return self._storage.get(_SESSIONS, session_id) is not None

    def append_event(self, session_id: str, event: AgentEvent) -> AgentEvent:
        with self._lock:
            self._ensure_session_unlocked(session_id)
            record = self._storage.get(_SESSIONS, session_id) or {}
            seq = _int_or(record.get("last_seq")) + 1
            filled = replace(event, session_id=session_id, seq=seq)
            self._storage.put(_EVENTS, f"{session_id}:{seq:08d}", _event_to_record(filled, seq))
            self._storage.set_json(_SESSIONS, session_id, "last_seq", seq)
            self._storage.set_json(_SESSIONS, session_id, "updated_at", _utc_now())
            return filled

    def read_events(self, session_id: str, limit: int = 200,
                    after_seq: int = -1) -> list[AgentEvent]:
        """历史回放：after_seq<0 返回最近 limit 条；>=0 返回 seq>after_seq 的最近 limit 条。"""
        if not self.session_exists(session_id):
            return []
        prefix = f"{session_id}:"
        matched: list[tuple[int, AgentEvent]] = []
        for item in self._storage.query(_EVENTS, limit=10_000):
            key = str(item.get("key") or "")
            if not key.startswith(prefix):
                continue
            seq = _int_or(item.get("seq"))
            if seq < 0 or (after_seq >= 0 and seq <= after_seq):
                continue
            matched.append((seq, _record_to_event(item)))
        matched.sort(key=lambda pair: pair[0])
        if limit > 0:
            matched = matched[-limit:]
        return [event for _, event in matched]

    def last_seq(self, session_id: str) -> int:
        record = self._storage.get(_SESSIONS, session_id)
        return _int_or(record.get("last_seq")) if record else -1

    def list_sessions(self, limit: int = 100, archived: bool | None = None,
                      query: str = "") -> list[dict[str, object]]:
        """会话元信息列表（最近更新优先，不含事件/消息体，供前端会话侧栏）。

        archived=None 返回全部；True/False 只返回对应归档态的会话。
        query 非空时按标题子串过滤（大小写不敏感）。
        """
        q = query.strip().lower()
        items = self._storage.query(_SESSIONS, limit=10_000)
        out: list[dict[str, object]] = []
        for item in items:
            session_id = str(item.get("key") or "")
            if not session_id:
                continue
            is_archived = bool(item.get("archived"))
            if archived is not None and is_archived != archived:
                continue
            title = str(item.get("title") or "")
            if q and q not in title.lower():
                continue
            out.append({
                "session_id": session_id,
                "title": title,
                "last_seq": _int_or(item.get("last_seq")),
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "archived": is_archived,
            })
        out.sort(key=lambda s: str(s.get("updated_at") or ""), reverse=True)
        return out[:limit]

    def append_message(self, session_id: str, message: dict[str, object]) -> None:
        """追加一条对话消息（user/assistant），按独立 msg_seq 单调编号。"""
        with self._lock:
            self._ensure_session_unlocked(session_id)
            record = self._storage.get(_SESSIONS, session_id) or {}
            msg_seq = _int_or(record.get("last_msg_seq")) + 1
            payload = {**message, "msg_seq": msg_seq, "created_at": _utc_now()}
            self._storage.put(_MESSAGES, f"{session_id}:{msg_seq:08d}", payload)
            self._storage.set_json(_SESSIONS, session_id, "last_msg_seq", msg_seq)
            self._storage.set_json(_SESSIONS, session_id, "updated_at", _utc_now())

    def read_messages(self, session_id: str, limit: int = 500,
                      before_seq: int = -1) -> list[dict[str, object]]:
        """按顺序回放会话消息（剥离 msg_seq/key 内部字段，只留业务字段）。

        before_seq<0 返回最近 limit 条；>=0 返回 msg_seq < before_seq 的最近 limit 条
        （供前端「加载更早」真分页）。
        """
        if not self.session_exists(session_id):
            return []
        prefix = f"{session_id}:"
        matched: list[tuple[int, dict[str, object]]] = []
        for item in self._storage.query(_MESSAGES, limit=10_000):
            key = str(item.get("key") or "")
            if not key.startswith(prefix):
                continue
            seq = _int_or(item.get("msg_seq"))
            if seq < 0 or (before_seq >= 0 and seq >= before_seq):
                continue
            # 保留 msg_seq（前端真分页游标），仅剥离内部 key
            matched.append((seq, {k: v for k, v in item.items() if k != "key"}))
        matched.sort(key=lambda pair: pair[0])
        if limit > 0:
            matched = matched[-limit:]
        return [record for _, record in matched]

    def rename_session(self, session_id: str, title: str) -> bool:
        """重命名会话标题；会话不存在返回 False。"""
        if not self.session_exists(session_id):
            return False
        with self._lock:
            record = self._storage.get(_SESSIONS, session_id)
            if record is None:
                return False
            record["title"] = title
            record["updated_at"] = _utc_now()
            self._storage.put(_SESSIONS, session_id, record)
        return True

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其事件/消息；会话不存在返回 False。"""
        if not self.session_exists(session_id):
            return False
        with self._lock:
            prefix = f"{session_id}:"
            for collection in (_EVENTS, _MESSAGES):
                for item in self._storage.query(collection, limit=10_000):
                    key = str(item.get("key") or "")
                    if key.startswith(prefix):
                        self._storage.delete(collection, key)
            return self._storage.delete(_SESSIONS, session_id)

    def delete_sessions(self, session_ids: list[str]) -> int:
        """批量删除会话；返回成功删除的会话数。"""
        removed = 0
        for session_id in session_ids:
            if self.delete_session(session_id):
                removed += 1
        return removed

    def archive_session(self, session_id: str, archived: bool) -> bool:
        """归档/取消归档会话；会话不存在返回 False。"""
        if not self.session_exists(session_id):
            return False
        with self._lock:
            record = self._storage.get(_SESSIONS, session_id)
            if record is None:
                return False
            record["archived"] = bool(archived)
            record["updated_at"] = _utc_now()
            self._storage.put(_SESSIONS, session_id, record)
        return True

    def usage_stats(self, session_id: str | None = None) -> dict[str, object]:
        """聚合 token 用量：session_id=None 统计全部，否则统计单会话。

        遍历 agent_messages，累加 assistant 消息里的 usage 字段，按 model 分组。
        """
        prefix = f"{session_id}:" if session_id else ""
        total = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "runs": 0}
        by_model: dict[str, dict[str, object]] = {}
        for item in self._storage.query(_MESSAGES, limit=10_000):
            key = str(item.get("key") or "")
            if session_id and not key.startswith(prefix):
                continue
            usage = item.get("usage")
            if not isinstance(usage, dict):
                continue
            model = str(item.get("model") or "unknown")
            entry = by_model.setdefault(model, {
                "model": model, "total_tokens": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "runs": 0,
            })
            t = _int_or(usage.get("total_tokens"), 0)
            p = _int_or(usage.get("prompt_tokens"), 0)
            c = _int_or(usage.get("completion_tokens"), 0)
            entry["total_tokens"] = int(entry["total_tokens"]) + t
            entry["prompt_tokens"] = int(entry["prompt_tokens"]) + p
            entry["completion_tokens"] = int(entry["completion_tokens"]) + c
            entry["runs"] = int(entry["runs"]) + 1
            total["total_tokens"] = int(total["total_tokens"]) + t
            total["prompt_tokens"] = int(total["prompt_tokens"]) + p
            total["completion_tokens"] = int(total["completion_tokens"]) + c
            total["runs"] = int(total["runs"]) + 1
        return {
            "total": total,
            "by_model": sorted(by_model.values(), key=lambda x: -int(x["total_tokens"])),
        }
