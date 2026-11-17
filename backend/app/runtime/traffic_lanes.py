# -*- coding: utf-8 -*-
"""Gateway 三车道：Control / Data / Best-effort / Stream 的可用性隔离。

规则（借鉴 EvoFlow，按本包路径重做）：

- **未知 /api/* 默认 DATA，绝不默认 Control** —— 新增慢接口天然被隔离，
  不用等一次 hang 再打补丁
- 过载时只 shed BEST_EFFORT，返回结构化 503 + x-plateau-lane / retry-after
- CONTROL（探活、登录、取消）与 STREAM（长连接）永不 shed、不占 REST 预算
- DATA 用信号量 clamp，BEST_EFFORT 用更小的信号量

实现为纯 ASGI 中间件（不用 BaseHTTPMiddleware，避免流式响应被缓冲），
只依赖 stdlib + starlette，可整段移植。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from enum import Enum

from starlette.datastructures import MutableHeaders


class Lane(str, Enum):
    CONTROL = "control"
    DATA = "data"
    BEST_EFFORT = "best_effort"
    STREAM = "stream"


@dataclass(frozen=True)
class LaneDecision:
    lane: Lane
    reason: str


_CONTROL_EXACT = frozenset(
    {
        "/health", "/health/liveness", "/health/ready", "/health/startup",
        "/api/ping", "/auth/status", "/auth/login",
        "/api/agent/models",
    }
)
_CONTROL_PREFIXES = ("/api/models", "/v1/models", "/auth/", "/api/runtime/auth/")
_CONTROL_SUFFIXES = ("/cancel", "/interrupt", "/tool-approval")
_STREAM_MARKERS = ("/stream", "/sse", "/v1/chat/completions")
_BEST_EFFORT_CONTAINS = (
    "/observability", "/logs", "/transcript", "/assets", "/static",
    "/api/runtime/status", "/api/runtime/services", "/api/runtime/storage",
    "/api/agent/sessions",
)

SHED_BODY = {
    "shed": True,
    "error": {"code": "GATEWAY_OVERLOAD_SHED", "message": "服务繁忙，请稍后重试"},
}


def classify_path(path: str, method: str = "GET") -> LaneDecision:
    """按路径分类；first match wins，CONTROL 最优先。"""
    p = path.split("?", 1)[0].rstrip("/") or "/"
    if p in _CONTROL_EXACT:
        return LaneDecision(Lane.CONTROL, "control_exact")
    if any(p.startswith(x) for x in _CONTROL_PREFIXES):
        return LaneDecision(Lane.CONTROL, "control_prefix")
    if any(p.endswith(x) for x in _CONTROL_SUFFIXES):
        return LaneDecision(Lane.CONTROL, "control_suffix")
    if any(x in p for x in _STREAM_MARKERS):
        return LaneDecision(Lane.STREAM, "stream_marker")
    if any(x in p for x in _BEST_EFFORT_CONTAINS):
        return LaneDecision(Lane.BEST_EFFORT, "best_effort_marker")
    if p.startswith("/api/") or p.startswith("/v1/"):
        return LaneDecision(Lane.DATA, "default_api_data")
    return LaneDecision(Lane.DATA, "default_unknown_data")


def overload_action(lane: Lane, overloaded: bool) -> str:
    """返回 allow / shed / clamp。"""
    if lane in (Lane.CONTROL, Lane.STREAM):
        return "allow"
    if lane is Lane.BEST_EFFORT and overloaded:
        return "shed"
    if lane is Lane.DATA:
        return "clamp"
    return "allow"


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 128) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, min(maximum, int(raw)))
    except ValueError:
        return default


class TrafficLaneMiddleware:
    """纯 ASGI 车道中间件。

    环境变量：PLATEAU_LANE_DATA_CONCURRENCY / PLATEAU_LANE_BEST_CONCURRENCY /
    PLATEAU_LANE_OVERLOAD_THRESHOLD。
    """

    def __init__(self, app) -> None:
        self.app = app
        self.data_concurrency = _env_int("PLATEAU_LANE_DATA_CONCURRENCY", 6)
        self.best_concurrency = _env_int("PLATEAU_LANE_BEST_CONCURRENCY", 2)
        self.overload_threshold = _env_int("PLATEAU_LANE_OVERLOAD_THRESHOLD", 12)
        self._data = asyncio.Semaphore(self.data_concurrency)
        self._best = asyncio.Semaphore(self.best_concurrency)
        self._inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        decision = classify_path(scope.get("path", ""), scope.get("method", "GET"))
        lane = decision.lane
        scope["plateau.traffic_lane"] = lane.value
        scope["plateau.lane_reason"] = decision.reason

        self._inflight += 1
        try:
            action = overload_action(lane, self._inflight >= self.overload_threshold)
            if action == "shed":
                await self._send_shed(send, lane, decision.reason)
                return
            wrapped = self._wrap_send(send, lane)
            sem = self._data if lane is Lane.DATA else (self._best if lane is Lane.BEST_EFFORT else None)
            if sem is None:
                await self.app(scope, receive, wrapped)
            else:
                async with sem:
                    await self.app(scope, receive, wrapped)
        finally:
            self._inflight -= 1

    @staticmethod
    def _wrap_send(send, lane: Lane):
        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["x-plateau-lane"] = lane.value
                headers.setdefault("x-plateau-shed", "0")
            await send(message)

        return wrapped

    @staticmethod
    async def _send_shed(send, lane: Lane, reason: str) -> None:
        body = json.dumps(
            {**SHED_BODY, "lane": lane.value, "reason": reason}, ensure_ascii=False
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"x-plateau-lane", lane.value.encode("ascii")),
            (b"x-plateau-shed", b"1"),
            (b"retry-after", b"2"),
            # shed 发生在 CORS 中间件之外，自带跨域头
            (b"access-control-allow-origin", b"*"),
        ]
        await send({"type": "http.response.start", "status": 503, "headers": headers})
        await send({"type": "http.response.body", "body": body})
