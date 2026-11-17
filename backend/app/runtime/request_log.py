# -*- coding: utf-8 -*-
"""请求级结构化日志（纯 ASGI，不缓冲流式响应）。

- request-id：透传合规的 x-request-id，否则生成；写回响应头，便于跨服务串联
- 记录 method / path / status / duration_ms / lane / shed / slow / 异常类型
- 不记录请求响应正文（避免敏感数据落盘，也避免破坏流式响应）
- query 只记参数名，不记值
- health 轮询与日志 tail 自身默认静音，异常/慢请求/shed 仍记 WARN

注册在 CORS 之外（最外层），这样 TrafficLaneMiddleware 直接返回的 shed 503
也能被记录并带上 x-request-id。
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from urllib.parse import parse_qsl

from starlette.datastructures import MutableHeaders

from .log_policy import env_flag
from .logging_setup import redact_text

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_HEALTH_PATHS = frozenset({"/health", "/health/liveness", "/health/ready"})


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _should_log(path: str, status: int, shed: bool, slow: bool) -> bool:
    if path.startswith("/api/logs"):
        return status >= 400 or shed or slow
    if not env_flag("PLATEAU_LOG_HEALTH_REQUESTS") and path in _HEALTH_PATHS:
        return status >= 500 or shed or slow
    return True


class RequestLogMiddleware:
    def __init__(self, app) -> None:
        self.app = app
        self._logger = logging.getLogger("plateau.request")
        self._slow_ms = _env_float("PLATEAU_SLOW_REQUEST_MS", 1500.0)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        path = str(scope.get("path", "")).split("?", 1)[0]
        method = str(scope.get("method", "GET"))
        lane = str(scope.get("plateau.traffic_lane", ""))
        headers_raw = scope.get("headers", [])
        incoming_id = ""
        user_agent = ""
        for key, value in headers_raw:
            lower = key.lower()
            if lower == b"x-request-id":
                incoming_id = value.decode("latin-1", errors="replace")
            elif lower == b"user-agent":
                user_agent = value.decode("latin-1", errors="replace")
        request_id = incoming_id if _REQUEST_ID_RE.match(incoming_id) else uuid.uuid4().hex[:16]
        scope["plateau.request_id"] = request_id

        client = scope.get("client")
        client_ip = str(client[0]) if isinstance(client, (tuple, list)) and client else ""
        query_raw = scope.get("query_string", b"")
        query_keys = sorted({
            key for key, _ in parse_qsl(
                query_raw.decode("latin-1", errors="replace"), keep_blank_values=True
            )
        })
        status = 500
        shed = False
        logged = False

        async def wrapped(message):
            nonlocal status, shed
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                shed = headers.get("x-plateau-shed") == "1"
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            self._logger.exception(
                "http_request",
                extra={
                    "event": "http_request", "request_id": request_id,
                    "method": method, "path": path, "query_keys": query_keys,
                    "status": 500, "duration_ms": duration_ms, "lane": lane,
                    "shed": shed, "slow": duration_ms >= self._slow_ms,
                    "error_type": type(exc).__name__, "client_ip": client_ip,
                    "user_agent": redact_text(user_agent),
                },
            )
            logged = True
            raise
        finally:
            if not logged:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                slow = duration_ms >= self._slow_ms
                if _should_log(path, status, shed, slow):
                    level = logging.WARNING if (status >= 500 or shed or slow) else logging.INFO
                    self._logger.log(
                        level,
                        "http_request",
                        extra={
                            "event": "http_request", "request_id": request_id,
                            "method": method, "path": path, "query_keys": query_keys,
                            "status": status, "duration_ms": duration_ms, "lane": lane,
                            "shed": shed, "slow": slow, "client_ip": client_ip,
                            "user_agent": redact_text(user_agent),
                        },
                    )
