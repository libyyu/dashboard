# -*- coding: utf-8 -*-
"""OpenAICompatProvider：消费标准 OpenAI 兼容 /v1/chat/completions SSE，翻译成统一事件。

覆盖智谱 / DeepSeek / Ollama / vLLM 等「注册表条目」供应商，与 CBOpenAIProvider
（cb 私有 /v1/agent/events 无损通道）并列，由编排层按 provider 的 api_type 分派。

翻译规则（有损还原，尽力保留思考流与工具调用）：
- delta.content                      → message_delta
- delta.reasoning_content / reasoning / thinking → thinking_delta（多字段自动探测）
- delta.tool_calls                   → 按 index 累积 → tool_call（arguments 为增量分片）
- finish_reason / usage（末帧或独立帧）→ run_done
- [DONE] / 流结束                     → run_done（无 finish_reason 时补 stop）
run_id / session_id / seq 由上层编排补全，这里只填 type 与 payload。

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
_THINKING_FIELDS = ("reasoning_content", "reasoning", "thinking")


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


class OpenAICompatProvider:
    """把标准 OpenAI 兼容 chat/completions 流解析成 AgentEvent 序列（同步生成器）。

    与 CBOpenAIProvider 相同的协作取消协议：``cancel_event`` 置位即停止拉取；
    ``upstream_holder`` 暴露底层响应给 cancel 端点跨线程 close()。
    """

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

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
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            method="POST",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if upstream_holder is not None:
                upstream_holder["response"] = response
            reducer = _ChunkReducer(model=model)
            for frame in _iter_sse_frames(response):
                if cancel_event is not None and cancel_event.is_set():
                    break
                for line in frame:
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    if raw == "[DONE]":
                        yield from reducer.finish()
                        continue
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(chunk, dict):
                        yield from reducer.feed(cast(dict[str, object], chunk))
            if upstream_holder is not None:
                upstream_holder.pop("response", None)
            # 上游没发 [DONE] 就断流：仍要补 run_done，让编排层落到 completed 终态
            if not reducer.done_emitted and not (cancel_event is not None and cancel_event.is_set()):
                yield from reducer.finish()


def _iter_sse_frames(response) -> Iterator[list[str]]:
    """按 SSE 规范把字节流切成事件帧：data: 行累积，空行分隔；忽略注释与心跳。"""
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


class _ChunkReducer:
    """单次 run 的 chunk 归并状态：thinking 多字段探测 + tool_calls 按 index 累积。"""

    def __init__(self, *, model: str) -> None:
        self.model = model
        self.finish_reason: str = ""
        self.usage: dict[str, object] | None = None
        self.done_emitted = False
        # tool_calls 累积表：index → {id, name, arguments}
        self._tools: dict[int, dict[str, str]] = {}

    def feed(self, chunk: dict[str, object]) -> Iterator[AgentEvent]:
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            self.usage = cast(dict[str, object], chunk["usage"])
        error = _as_dict(chunk.get("error"))
        if error:
            yield AgentEvent(type="error", payload={
                "message": str(error.get("message") or "上游返回错误"),
                "code": str(error.get("code") or "upstream_error"),
            })
            return
        choices = _as_list(chunk.get("choices"))
        if not choices:
            return
        choice = _as_dict(choices[0])
        delta = _as_dict(choice.get("delta"))
        if not delta:
            # 非流式错误体或空 delta 帧，跳过
            _ = choice
            return
        for field in _THINKING_FIELDS:
            thinking = delta.get(field)
            if isinstance(thinking, str) and thinking:
                yield AgentEvent(type="thinking_delta", payload={"text": thinking})
                break
        content = delta.get("content")
        if isinstance(content, str) and content:
            yield AgentEvent(type="message_delta", payload={"text": content})
        elif isinstance(content, list):
            # 少数网关把 content 发成分段数组
            for part in content:
                text = _as_dict(part).get("text")
                if isinstance(text, str) and text:
                    yield AgentEvent(type="message_delta", payload={"text": text})
        for call in _as_list(delta.get("tool_calls")):
            yield from self._feed_tool_call(_as_dict(call))
        finish = choice.get("finish_reason")
        if isinstance(finish, str) and finish:
            self.finish_reason = finish
            if finish != "stop":
                # stop 的 run_done 统一在流末尾发，避免 usage 帧还没到就收尾
                yield from self.finish()

    def _feed_tool_call(self, call: dict[str, object]) -> Iterator[AgentEvent]:
        index = call.get("index")
        index = index if isinstance(index, int) else len(self._tools)
        function = _as_dict(call.get("function"))
        slot = self._tools.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call_id = call.get("id")
        if isinstance(call_id, str) and call_id:
            slot["id"] = call_id
        name = function.get("name")
        if isinstance(name, str) and name:
            slot["name"] = name
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            slot["arguments"] += arguments
        yield AgentEvent(type="tool_call", payload={
            "id": slot["id"], "name": slot["name"], "arguments": slot["arguments"],
        })

    def finish(self) -> Iterator[AgentEvent]:
        if self.done_emitted:
            return
        self.done_emitted = True
        yield AgentEvent(type="run_done", payload={
            "finish_reason": self.finish_reason or "stop",
            "usage": self.usage,
            "model": self.model,
        })
