# -*- coding: utf-8 -*-
"""CBOpenAIProvider：消费 cb sidecar 的原生事件出口 /v1/agent/events，无损映射。

cb 已在内部把 SDK 消息翻译成统一事件（TextDelta / ThinkingDelta / ToolCallEvent /
FinishEvent / ErrorEvent），本 provider 只做「cb 事件 JSON → AgentEvent」的一对一映射，
不做 OpenAI chunk 的有损还原。run_id / session_id / seq 由上层编排补全，这里只填 type
与 payload。

依赖仅 stdlib（urllib），契合后端零第三方依赖 + PyInstaller 冻结约束。
"""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from typing import cast

from app.core.agent import AgentEvent

_STREAM_TIMEOUT = 1800.0
_READ_SIZE = 8192


def _map_event(obj: dict[str, object]) -> AgentEvent | None:
    kind = str(obj.get("type") or "")
    if kind == "text":
        return AgentEvent(type="message_delta", payload={"text": str(obj.get("text") or "")})
    if kind == "thinking":
        return AgentEvent(type="thinking_delta", payload={"text": str(obj.get("text") or "")})
    if kind == "tool_call":
        return AgentEvent(type="tool_call", payload={
            "id": str(obj.get("id") or ""),
            "name": str(obj.get("name") or ""),
            "arguments": str(obj.get("arguments") or ""),
        })
    if kind == "finish":
        return AgentEvent(type="run_done", payload={
            "finish_reason": str(obj.get("reason") or "stop"),
            "usage": obj.get("usage") if isinstance(obj.get("usage"), dict) else None,
            "model": str(obj.get("model") or ""),
        })
    if kind == "error":
        return AgentEvent(type="error", payload={
            "message": str(obj.get("message") or ""),
            "code": str(obj.get("code") or "upstream_error"),
        })
    return None


class CBOpenAIProvider:
    """把 cb 的 /v1/agent/events 流解析成 AgentEvent 序列（同步生成器）。

    ``cancel_event`` 为协作式取消：每读一个 chunk 前检查，置位后停止拉取。
    ``upstream_holder`` 用于把底层响应句柄暴露给上层：cancel 端点在另一线程
    close() 它，让阻塞的 read 立即抛异常，避免等待 cb 的下一个心跳（15s）。
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def iter_events(
        self,
        model: str,
        messages: list[dict[str, object]],
        *,
        cancel_event=None,
        upstream_holder: dict[str, object] | None = None,
        timeout: float = _STREAM_TIMEOUT,
        reasoning_effort: str = "",
    ) -> Iterator[AgentEvent]:
        payload: dict[str, object] = {"model": model, "messages": messages, "stream": True}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/agent/events",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if upstream_holder is not None:
                upstream_holder["response"] = response
            for frame in _iter_sse_frames(response):
                if cancel_event is not None and cancel_event.is_set():
                    break
                yield from _parse_frame(frame)
            if upstream_holder is not None:
                upstream_holder.pop("response", None)


def _iter_sse_frames(response) -> Iterator[list[str]]:
    """按 SSE 规范把字节流切成事件帧：data: 行累积，空行分隔；忽略注释与 : ping。"""
    buffer = b""
    while True:
        chunk = response.read(_READ_SIZE)
        if not chunk:
            break
        buffer += chunk
        while True:
            idx = buffer.find(b"\n\n")
            if idx < 0:
                break
            block = buffer[:idx].decode("utf-8", errors="replace")
            buffer = buffer[idx + 2:]
            lines = [ln for ln in block.splitlines() if ln.startswith("data:")]
            if lines:
                yield lines


def _parse_frame(lines: list[str]) -> Iterator[AgentEvent]:
    for line in lines:
        raw = line[len("data:"):].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        event = _map_event(cast(dict[str, object], obj))
        if event is not None:
            yield event
