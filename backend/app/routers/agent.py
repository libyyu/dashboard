# -*- coding: utf-8 -*-
"""智能体路由：薄编排的 run 生命周期 + 统一事件流。

- POST /api/agent/runs/stream        STREAM，起 run，SSE 吐 AgentEvent（帧带 id:{seq}）
- POST /api/agent/runs/{id}/cancel   CONTROL，协作式取消（永不 shed）
- GET  /api/agent/sessions/{id}/events  BEST_EFFORT，历史回放 + after_seq 增量续读

编排只做四件事：provider 事件补全生命周期字段 → seq 持久化 → 取消检查 → SSE 编码。
不实现工具循环 / 多智能体（core.agent 的 Orchestrator / MCPToolAdapter 留待后续）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
import urllib.error
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from app.adapters.cb_openai_provider import CBOpenAIProvider
from app.adapters.codebuddy_cli_provider import CodeBuddyCliProvider
from app.adapters.openai_compat_provider import OpenAICompatProvider
from app.adapters.session_store import SqliteSessionStore
from app.core.agent import AgentEvent
from app.core.context import trim_messages
from app.core.errors import error_response
from app.routers.model import _default_provider, _find_provider, _request_json
from app.adapters.storage_factory import get_storage
from app.runtime import ports

router = APIRouter(prefix="/api/agent", tags=["agent"])

_HEARTBEAT = ": ping\n\n"

# 上传附件大小上限（与前端一致），超出返回 FILE_TOO_LARGE
_MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB


def _uploads_dir() -> Path:
    d = ports.home_dir() / "profiles" / ports.profile_id() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d

# 登录类 provider 的协作取消/鉴权契约：auth/status 只在显式 failed 时拦截。
# 「unknown / pending」不拦——实测 cb 携 api key 时 unknown 也能正常出流。
_AUTH_BLOCKING_STATUS = frozenset({"failed"})
# 上游 error 事件中视为「需要登录」的码，统一改写为 PROVIDER_AUTH_REQUIRED
_AUTH_ERROR_CODES = frozenset({
    "CB_LOGIN_FAILED", "PROVIDER_AUTH_FAILED", "LOGIN_REQUIRED",
    "UNAUTHORIZED", "401",
})

# --------------------------------------------------------------------------- #
# CLI 会话续用（省 token）：应用 session_id → 稳定 CLI session 的映射。
# 同模型 + 同 provider + 用户消息序列未变时，用 --resume 只发增量；否则全量重发。
# 绑定只驻内存：应用重启后自动回到全量（一次），不跨进程假设 CLI 转写仍存在。
# --------------------------------------------------------------------------- #
_cli_bindings: dict[str, dict[str, str]] = {}
_cli_bindings_lock = threading.Lock()


def _cli_fingerprint(users: list[dict[str, object]]) -> str:
    """用户消息序列指纹：历史被编辑/删除时指纹变，触发放弃 resume。"""
    payload = [{"content": m.get("content")} for m in users]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _get_cli_binding(session_id: str) -> dict[str, str] | None:
    with _cli_bindings_lock:
        return _cli_bindings.get(session_id)


def _set_cli_binding(session_id: str, binding: dict[str, str] | None) -> None:
    with _cli_bindings_lock:
        if binding is None:
            _cli_bindings.pop(session_id, None)
        else:
            _cli_bindings[session_id] = binding


def _create_adapter(provider_info: dict[str, object]) -> object:
    """按 provider 的 api_type 分派适配器；未知名走标准 OpenAI 兼容面。"""
    base_url = str(provider_info["base_url"])
    api_type = str(provider_info.get("api_type") or "openai-completions")
    api_key = str(provider_info.get("api_key") or "")
    if api_type == "agent-events":
        return CBOpenAIProvider(base_url)
    if api_type == "openai-completions":
        return OpenAICompatProvider(base_url, api_key)
    if api_type == "cli":
        # 本地 CodeBuddy CLI：本机进程，无需 base_url/api_key；工具/工作目录留待 agent 阶段
        return CodeBuddyCliProvider()
    # 未来协议（cli-* 等）尚未注册 adapter：按标准面兜底，让 provider 自己决定兼容
    return OpenAICompatProvider(base_url, api_key)


def _auth_login_status(provider_info: dict[str, object]) -> str:
    """login 类 provider 的登录态探测；不可达视为 unknown（不拦流，交给上游报错）。"""
    if provider_info.get("auth") != "login":
        return ""
    result = _request_json(
        f"{provider_info['base_url']}/auth/status", 5.0,
        headers=_proxy_auth_headers(provider_info),
    )
    if not isinstance(result, dict):
        return "unknown"
    status = str(result.get("login_status") or result.get("status") or "unknown")
    return status.lower()


def _proxy_auth_headers(provider_info: dict[str, object]) -> dict[str, str]:
    # 注册表条目的 api_key 由壳注入；sidecar 凭证由插件自管
    key = str(provider_info.get("api_key") or "").strip()
    if key and provider_info.get("source") == "registry":
        return {"Authorization": f"Bearer {key}"}
    return {}


def _rewrite_error_code(payload: dict[str, object]) -> dict[str, object]:
    """上游 error 事件的登录类错误码统一改写为 PROVIDER_AUTH_REQUIRED，供前端登录引导。"""
    code = str(payload.get("code") or "")
    if code in _AUTH_ERROR_CODES:
        payload = {**payload, "code": "PROVIDER_AUTH_REQUIRED"}
    return payload


@dataclass
class RunState:
    run_id: str
    session_id: str
    trace_id: str
    request_id: str
    status: str = "running"  # running | cancelling | cancelled | completed | failed
    cancel_event: threading.Event = field(default_factory=threading.Event)
    upstream_holder: dict[str, object] = field(default_factory=dict)


_runs: dict[str, RunState] = {}
_runs_lock = threading.Lock()

_session_store: SqliteSessionStore | None = None
_session_store_lock = threading.Lock()


def _get_session_store() -> SqliteSessionStore | None:
    global _session_store
    storage = get_storage()
    if storage is None:
        return None
    if _session_store is None:
        with _session_store_lock:
            if _session_store is None:
                _session_store = SqliteSessionStore(storage)
    return _session_store


def _register(state: RunState) -> None:
    with _runs_lock:
        _runs[state.run_id] = state


def _unregister(run_id: str) -> None:
    with _runs_lock:
        _runs.pop(run_id, None)


def _get_run(run_id: str) -> RunState | None:
    with _runs_lock:
        return _runs.get(run_id)


def _new_id() -> str:
    return uuid.uuid4().hex


def _sse(event: AgentEvent) -> str:
    data = json.dumps(
        {
            "type": event.type,
            "seq": event.seq,
            "run_id": event.run_id,
            "session_id": event.session_id,
            "trace_id": event.trace_id,
            "request_id": event.request_id,
            "payload": event.payload,
        },
        ensure_ascii=False,
    )
    return f"id: {event.seq}\ndata: {data}\n\n"


async def _json_body(request: Request) -> dict[str, object]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001 - 空/非 JSON 体一律当空
        return {}


def _persist(store: SqliteSessionStore, state: RunState, event: AgentEvent) -> AgentEvent:
    """补全生命周期字段 → 持久化（分配 seq），返回落库后的完整事件。"""
    filled = replace(
        event,
        run_id=state.run_id,
        session_id=state.session_id,
        trace_id=state.trace_id,
        request_id=state.request_id,
    )
    return store.append_event(state.session_id, filled)


def _emit(store: SqliteSessionStore, state: RunState, event: AgentEvent) -> str:
    """持久化 + SSE 帧（前台 tail 推送用）。"""
    return _sse(_persist(store, state, event))


def _execute_run(
    store: SqliteSessionStore,
    state: RunState,
    adapter: object,
    model: str,
    forward_messages: list[dict[str, object]],
    reasoning_effort: str,
    provider_name: str,
    resume: bool = False,
    cli_session_id: str = "",
    commit_binding: dict[str, str] | None = None,
) -> None:
    """后台执行 run：消费 provider 事件 → 持久化 → 收尾落库 assistant 消息。

    与前台 SSE tail 解耦：客户端连接断开不影响本线程继续跑完，事件按 seq 落库，
    前端可经 GET /sessions/{id}/events?after_seq= 断点续读剩余事件。
    """
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    calls: list[dict[str, object]] = []
    usage: dict[str, object] | None = None
    _persist(store, state, AgentEvent(
        type="run_started", payload={"model": model, "provider": provider_name},
    ))
    try:
        events = adapter.iter_events(  # type: ignore[attr-defined]
            model, forward_messages,
            cancel_event=state.cancel_event, upstream_holder=state.upstream_holder,
            reasoning_effort=reasoning_effort,
            resume=resume, session_id=cli_session_id,
        ) if isinstance(adapter, CodeBuddyCliProvider) else adapter.iter_events(  # type: ignore[attr-defined]
            model, forward_messages,
            cancel_event=state.cancel_event, upstream_holder=state.upstream_holder,
            reasoning_effort=reasoning_effort,
        )
        for raw in events:
            if raw.type == "message_delta":
                content_parts.append(_as_text(raw.payload))
            elif raw.type == "thinking_delta":
                thinking_parts.append(_as_text(raw.payload))
            elif raw.type == "tool_call":
                _accumulate_tool_call(calls, raw.payload)
            elif raw.type == "tool_result":
                _accumulate_tool_result(calls, raw.payload)
            if raw.type == "run_done" and isinstance(raw.payload.get("usage"), dict):
                usage = cast(dict[str, object], raw.payload["usage"])
            if raw.type == "error":
                raw = replace(raw, payload=_rewrite_error_code(dict(raw.payload)))
            _persist(store, state, raw)
    except Exception as exc:  # noqa: BLE001 - 上游/连接错误统一转事件
        if state.cancel_event.is_set():
            state.status = "cancelled"
            _persist(store, state, AgentEvent(type="cancelled", payload={}))
        else:
            state.status = "failed"
            _persist(store, state, AgentEvent(type="error", payload={
                "message": f"provider 不可达或流中断：{exc}", "code": "PROVIDER_UNAVAILABLE",
            }))
    else:
        if state.cancel_event.is_set():
            state.status = "cancelled"
            _persist(store, state, AgentEvent(type="cancelled", payload={}))
        else:
            state.status = "completed"
    finally:
        if state.status == "completed" and commit_binding is not None:
            # 本轮成功后固化绑定：下次同条件走 resume 增量续传
            _set_cli_binding(state.session_id, commit_binding)
        _unregister(state.run_id)
        if content_parts or thinking_parts or calls:
            store.append_message(state.session_id, {
                "role": "assistant",
                "content": "".join(content_parts),
                "thinking": "".join(thinking_parts) or None,
                "tool_calls": calls or None,
                "provider": provider_name,
                "model": model,
                "usage": usage,
            })


@router.post("/runs/stream", response_model=None)
async def agent_run_stream(request: Request) -> StreamingResponse | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")

    body = await _json_body(request)
    session_id = str(body.get("session_id") or "").strip()
    messages = body.get("messages")
    if not session_id:
        return error_response("AGENT_NOT_READY", 400, "缺少 session_id")
    if not isinstance(messages, list) or not messages:
        return error_response("AGENT_NOT_READY", 400, "messages 不能为空")
    model = str(body.get("model") or "").strip()
    reasoning_effort = str(body.get("reasoning_effort") or "").strip()
    title = str(body.get("title") or "").strip()
    provider_name = str(body.get("provider") or "").strip()
    system = str(body.get("system") or "").strip()

    # provider 解析：请求指定 > 目录默认；目录来自注册表 KV ∪ 插件扫描
    provider_info = _find_provider(provider_name) if provider_name else _default_provider()
    if provider_info is None:
        if provider_name:
            return error_response("PLUGIN_NOT_FOUND", 404, f"未知 model provider：{provider_name}")
        return error_response("AGENT_NOT_READY", 503, "无可用 model provider（请安装模型插件或在注册表登记供应商）")
    # login 类登录前置检查：仅显式 failed 拦截（unknown/pending 放行，交由上游报错）
    if _auth_login_status(provider_info) in _AUTH_BLOCKING_STATUS:
        return error_response("PROVIDER_AUTH_REQUIRED", 401, f"provider {provider_info['name']} 需要登录")

    run_id = _new_id()
    trace_id = _new_id()
    request_id = str(request.headers.get("x-request-id") or _new_id())
    state = RunState(run_id=run_id, session_id=session_id, trace_id=trace_id, request_id=request_id)
    _register(state)

    adapter = _create_adapter(provider_info)
    resolved_provider = str(provider_info["name"])
    store.ensure_session(session_id, title=title or _derive_title(messages))
    # 持久化本轮用户消息（含附件元信息；assistant 回复在流结束后按增量拼接再落库），
    # 使会话可整体回放，不依赖前端内存态。
    user_message = _last_user_message(messages)
    if user_message is not None:
        store.append_message(session_id, user_message)
    # 透传前剥离 attachments 字段（模型协议不感知，仅用于本地回放）
    stripped = _strip_attachments(messages)
    users = [m for m in stripped if isinstance(m, dict) and m.get("role") == "user"]

    # CLI 会话续用：同模型 + 同 provider + 用户消息序列未变 → --resume 只发增量。
    # 其余情况全量重发（含首次 / 切模型 / 切 provider / 历史被编辑）。
    resume = False
    cli_session_id = ""
    commit_binding: dict[str, str] | None = None
    if isinstance(adapter, CodeBuddyCliProvider):
        binding = _get_cli_binding(session_id)
        if binding and binding["model"] == model and binding["provider"] == resolved_provider \
                and _cli_fingerprint(users[:-1]) == binding["fingerprint"]:
            resume = True
            cli_session_id = binding["cli_session_id"]
        if resume:
            # 增量：只发本轮新消息（含图片）；历史由 CLI --resume 自建
            forward_messages = users[-1:]
        else:
            forward_messages = stripped
            if system:
                forward_messages = [{"role": "system", "content": system}, *forward_messages]
            forward_messages = trim_messages(forward_messages)
            cli_session_id = _new_id()
            commit_binding = {
                "cli_session_id": cli_session_id,
                "model": model,
                "provider": resolved_provider,
                "fingerprint": _cli_fingerprint(users),
            }
    else:
        # 非 CLI provider：维持全量透传
        forward_messages = stripped
        if system:
            forward_messages = [{"role": "system", "content": system}, *forward_messages]
        forward_messages = trim_messages(forward_messages)

    # 本次 run 的事件基线（run 启动前的最后 seq），tail 只推送本次 run 的新事件
    baseline_seq = store.last_seq(session_id)

    # run 在后台线程执行：事件按 seq 落库，与 SSE 连接解耦（断点续传的前提）
    run_thread = threading.Thread(
        target=_execute_run,
        args=(store, state, adapter, model, forward_messages, reasoning_effort, resolved_provider,
              resume, cli_session_id, commit_binding),
        daemon=True,
    )
    run_thread.start()

    def tail() -> object:
        """SSE tail：轮询已持久化事件并推送；连接断开即停止，不影响后台 run 继续跑完。"""
        after_seq = baseline_seq
        while True:
            events = store.read_events(session_id, limit=500, after_seq=after_seq)
            for event in events:
                after_seq = event.seq
                yield _sse(event)
            if state.status in ("completed", "failed", "cancelled"):
                break
            if not run_thread.is_alive() and state.status == "running":
                # 后台线程异常退出但未标记终态：兜底结束，避免无限轮询
                break
            time.sleep(0.05)

    return StreamingResponse(
        tail(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{id}/cancel", response_model=None)
async def agent_cancel(id: str) -> dict[str, object] | JSONResponse:
    state = _get_run(id)
    if state is None:
        return error_response("AGENT_RUN_NOT_FOUND", 404, f"run 不存在或已结束：{id}")
    state.status = "cancelling"
    state.cancel_event.set()
    upstream = state.upstream_holder.get("response")
    if upstream is not None:
        # 关闭底层响应，让阻塞中的 read 立即抛异常，不必等 cb 的下一个心跳
        try:
            upstream.close()
        except Exception:  # noqa: BLE001
            pass
    process = state.upstream_holder.get("process")
    if process is not None:
        # CodeBuddyCliProvider 把 subprocess 句柄暴露在此，terminate 让 stdout readline 立即返回
        try:
            process.terminate()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "run_id": id, "status": "cancelling"}


@router.get("/sessions/{id}/events", response_model=None)
async def agent_session_events(
    id: str, request: Request,
) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    if not store.session_exists(id):
        return error_response("AGENT_SESSION_NOT_FOUND", 404, f"会话不存在：{id}")

    after_seq = -1
    last_event_id = request.headers.get("last-event-id", "")
    if last_event_id.isdigit():
        after_seq = int(last_event_id)
    elif request.query_params.get("after_seq", "").lstrip("-").isdigit():
        after_seq = int(request.query_params.get("after_seq"))
    limit = int(request.query_params.get("limit") or 200)

    events = store.read_events(id, limit=limit, after_seq=after_seq)
    return {"session_id": id, "after_seq": after_seq, "events": [
        {
            "type": e.type, "seq": e.seq, "run_id": e.run_id, "session_id": e.session_id,
            "trace_id": e.trace_id, "request_id": e.request_id, "payload": e.payload,
        }
        for e in events
    ]}


@router.get("/sessions", response_model=None)
async def agent_sessions(request: Request) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    raw_limit = request.query_params.get("limit", "")
    limit = 100
    if raw_limit.isdigit():
        limit = max(1, min(int(raw_limit), 500))
    archived: bool | None = None
    archived_raw = request.query_params.get("archived", "")
    if archived_raw in ("true", "1"):
        archived = True
    elif archived_raw in ("false", "0"):
        archived = False
    query = request.query_params.get("q", "").strip()
    return {"sessions": store.list_sessions(limit=limit, archived=archived, query=query)}


@router.get("/sessions/{id}/messages", response_model=None)
async def agent_session_messages(id: str, request: Request) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    if not store.session_exists(id):
        return error_response("AGENT_SESSION_NOT_FOUND", 404, f"会话不存在：{id}")
    limit = int(request.query_params.get("limit") or 500)
    before_raw = request.query_params.get("before_seq", "")
    before_seq = int(before_raw) if before_raw.lstrip("-").isdigit() else -1
    messages = store.read_messages(id, limit=limit, before_seq=before_seq)
    has_more = len(messages) >= limit
    next_before_seq = -1
    if messages:
        first_seq = messages[0].get("msg_seq")
        next_before_seq = first_seq if isinstance(first_seq, int) else -1
    return {
        "session_id": id,
        "messages": messages,
        "has_more": has_more,
        "next_before_seq": next_before_seq,
    }


@router.patch("/sessions/{id}", response_model=None)
async def agent_session_patch(id: str, request: Request) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    body = await _json_body(request)
    if "title" in body:
        title = str(body.get("title") or "").strip()
        if not store.rename_session(id, title):
            return error_response("AGENT_SESSION_NOT_FOUND", 404, f"会话不存在：{id}")
        return {"ok": True, "session_id": id, "title": title}
    if "archived" in body:
        archived = bool(body.get("archived"))
        if not store.archive_session(id, archived):
            return error_response("AGENT_SESSION_NOT_FOUND", 404, f"会话不存在：{id}")
        return {"ok": True, "session_id": id, "archived": archived}
    return error_response("INVALID_SOURCE", 400, "缺少 title 或 archived 字段")


@router.delete("/sessions/{id}", response_model=None)
async def agent_session_delete(id: str) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    if not store.delete_session(id):
        return error_response("AGENT_SESSION_NOT_FOUND", 404, f"会话不存在：{id}")
    return {"ok": True, "session_id": id}


@router.post("/sessions/batch-delete", response_model=None)
async def agent_sessions_batch_delete(request: Request) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    body = await _json_body(request)
    ids = body.get("session_ids")
    if not isinstance(ids, list) or not ids:
        return error_response("INVALID_SOURCE", 400, "缺少 session_ids")
    removed = store.delete_sessions([str(i) for i in ids])
    return {"ok": True, "removed": removed}


@router.get("/usage", response_model=None)
async def agent_usage(request: Request) -> dict[str, object] | JSONResponse:
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    session_id = request.query_params.get("session_id", "").strip() or None
    return store.usage_stats(session_id=session_id)


@router.post("/sessions/import", response_model=None)
async def agent_session_import(request: Request) -> dict[str, object] | JSONResponse:
    """导入会话：JSON {title, messages:[{role,content,...}]}，按消息顺序重建。"""
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    body = await _json_body(request)
    title = str(body.get("title") or "").strip()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return error_response("INVALID_SOURCE", 400, "messages 不能为空")
    session_id = _new_id()
    store.ensure_session(session_id, title=title or "导入会话")
    fields = ("role", "content", "thinking", "tool_calls", "provider", "model", "attachments", "usage")
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in ("user", "assistant", "system", "tool"):
            continue
        record: dict[str, object] = {k: message[k] for k in fields if k in message}
        record["role"] = role
        if not record.get("content"):
            record["content"] = ""
        store.append_message(session_id, record)
    return {"ok": True, "session_id": session_id, "title": title or "导入会话"}


def _generate_title_sync(adapter: object, model: str, prompt: str) -> str:
    """同步跑一次简短的标题生成，收集 message_delta 拼成标题。"""
    parts: list[str] = []
    try:
        for raw in adapter.iter_events(  # type: ignore[attr-defined]
            model, [{"role": "user", "content": prompt}], reasoning_effort="",
        ):
            if raw.type == "message_delta":
                parts.append(_as_text(raw.payload))
            elif raw.type == "error":
                return ""
    except Exception:  # noqa: BLE001 - 标题生成失败降级为空
        return ""
    return "".join(parts).strip()


@router.post("/sessions/{id}/title", response_model=None)
async def agent_generate_title(id: str, request: Request) -> dict[str, object] | JSONResponse:
    """用当前 provider 为会话生成简短标题（LLM 摘要）。"""
    store = _get_session_store()
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    if not store.session_exists(id):
        return error_response("AGENT_SESSION_NOT_FOUND", 404, f"会话不存在：{id}")
    body = await _json_body(request)
    provider_name = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    provider_info = _find_provider(provider_name) if provider_name else _default_provider()
    if provider_info is None:
        return error_response("AGENT_NOT_READY", 503, "无可用 model provider")
    messages = store.read_messages(id, limit=20)
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            lines.append(f"{'用户' if role == 'user' else '助手'}: {content.strip()[:200]}")
    if not lines:
        return error_response("AGENT_NOT_READY", 400, "会话无内容")
    prompt = (
        "请用不超过 15 个字概括下面这段对话的主题，只输出标题本身，不要任何解释或标点：\n\n"
        + "\n".join(lines)
    )
    adapter = _create_adapter(provider_info)
    title = (await asyncio.to_thread(_generate_title_sync, adapter, model, prompt)).strip()[:40]
    if not title:
        return error_response("PROVIDER_UNAVAILABLE", 503, "标题生成失败")
    store.rename_session(id, title)
    return {"ok": True, "session_id": id, "title": title}


@router.post("/files", response_model=None)
async def agent_upload_file(request: Request) -> dict[str, object] | JSONResponse:
    """聊天附件上传：JSON base64（避免 multipart 依赖），落 profile/uploads 目录。"""
    body = await _json_body(request)
    name = str(body.get("name") or "upload.bin")
    safe_name = Path(name).name
    mime = str(body.get("mime") or "application/octet-stream")
    encoded = str(body.get("data") or "")
    if not encoded:
        return error_response("INVALID_SOURCE", 400, "缺少文件内容")
    try:
        raw = base64.b64decode(encoded)
    except Exception:  # noqa: BLE001 - 非法 base64 一律按缺内容处理
        return error_response("INVALID_SOURCE", 400, "文件内容不是合法 base64")
    if len(raw) > _MAX_UPLOAD_SIZE:
        return error_response("FILE_TOO_LARGE", 413, "文件超过 20MB 限制")
    file_id = _new_id()
    dest = _uploads_dir() / f"{file_id}-{safe_name}"
    dest.write_bytes(raw)
    return {
        "file_id": file_id,
        "name": safe_name,
        "mime": mime,
        "size": len(raw),
        "url": f"/api/agent/files/{file_id}",
    }


@router.get("/files/{file_id}", response_model=None)
async def agent_download_file(file_id: str) -> FileResponse | JSONResponse:
    # file_id 由服务端 _new_id() 生成（32 位 hex），严格校验防路径穿越
    if len(file_id) != 32 or any(c not in "0123456789abcdef" for c in file_id):
        return error_response("FILE_NOT_FOUND", 404, f"文件不存在：{file_id}")
    matches = sorted(_uploads_dir().glob(f"{file_id}-*"))
    if not matches:
        return error_response("FILE_NOT_FOUND", 404, f"文件不存在：{file_id}")
    path = matches[0]
    original = path.name[len(file_id) + 1:]
    return FileResponse(str(path), filename=original)


def _as_text(payload: dict[str, object], key: str = "text") -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _content_text(content: object) -> str:
    """把 user 消息的 content（字符串或多模态 parts 数组）归一为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(parts)
    return ""


def _last_user_message(messages: object) -> dict[str, object] | None:
    """取请求里最新一条 user 消息，归一为可持久化记录（含附件元信息）。"""
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        record: dict[str, object] = {
            "role": "user",
            "content": _content_text(message.get("content")),
        }
        attachments = message.get("attachments")
        if isinstance(attachments, list) and attachments:
            record["attachments"] = _sanitize_attachments(attachments)
        return record
    return None


# 附件持久化字段白名单：只留回放所需字段，丢弃前端瞬态句柄（如 File）
_ATTACHMENT_FIELDS = ("id", "name", "mime", "kind", "size", "dataUrl", "text", "fileId", "url")


def _sanitize_attachments(attachments: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    if not isinstance(attachments, list):
        return out
    for item in attachments:
        if not isinstance(item, dict):
            continue
        out.append({k: item[k] for k in _ATTACHMENT_FIELDS if k in item})
    return out


def _strip_attachments(messages: object) -> list[dict[str, object]]:
    """透传模型前剥离 attachments 字段（模型协议不感知，仅用于本地回放）。"""
    out: list[dict[str, object]] = []
    if not isinstance(messages, list):
        return out
    for message in messages:
        if not isinstance(message, dict):
            continue
        out.append({k: v for k, v in message.items() if k != "attachments"})
    return out


def _accumulate_tool_call(calls: list[dict[str, object]], payload: dict[str, object]) -> None:
    call_id = str(payload.get("id") or "")
    name = str(payload.get("name") or "")
    if not call_id and not name:
        return
    existing = next(
        (c for c in calls if c.get("id") == call_id or (call_id == "" and c.get("name") == name)),
        None,
    )
    arguments = str(payload.get("arguments") or "")
    if existing is not None:
        if arguments:
            existing["arguments"] = arguments
    else:
        calls.append({"id": call_id, "name": name, "arguments": arguments})


def _accumulate_tool_result(calls: list[dict[str, object]], payload: dict[str, object]) -> None:
    call_id = str(payload.get("id") or "")
    result = str(payload.get("result") or "")
    for call in calls:
        if call.get("id") == call_id and call_id:
            call["result"] = result
            break


def _derive_title(messages: list[object]) -> str:
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:40]
    return ""
