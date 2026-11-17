# -*- coding: utf-8 -*-
"""三层探活：liveness / ready / health。

- liveness：只回答「进程还活着」，故意用 sync def 走 threadpool，event loop 卡住也能答
- ready：回答「核心路由已挂载 + 依赖服务可达」，未就绪返回 503 并带 phase
- health：综合状态；可选依赖不可用时给 degraded 而不是 unhealthy

必须在业务路由之前注册，避免 liveness 被慢路由拖住。
"""
from __future__ import annotations

import asyncio
import os
import urllib.request

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

SERVICE_NAME = "plateau-dashboard"


def _cb_probe() -> bool | None:
    """配置了 PLATEAU_CB_HEALTH_URL 时探测 cb；未配置返回 None（不参与判定）。"""
    url = os.environ.get("PLATEAU_CB_HEALTH_URL", "").strip()
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 探活失败一律视为不可用
        return False


def register_health_routes(app: FastAPI) -> None:
    @app.get("/health/liveness", tags=["health"])
    def liveness() -> dict[str, object]:
        return {"status": "alive", "service": SERVICE_NAME, "pid": os.getpid()}

    @app.get("/health/ready", tags=["health"], response_model=None)
    async def readiness(request: Request) -> dict[str, object] | JSONResponse:
        state = request.app.state
        core = bool(getattr(state, "core_routers_registered", False))
        cb = await asyncio.to_thread(_cb_probe)
        ready = core and cb is not False
        body = {
            "status": "ready" if ready else "not_ready",
            "service": SERVICE_NAME,
            "phase": str(getattr(state, "startup_phase", "unknown")),
            "core_routers": core,
            "extended_routers": bool(getattr(state, "extended_routers_registered", False)),
            "cb": cb,
        }
        return body if ready else JSONResponse(status_code=503, content=body)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, object]:
        cb = await asyncio.to_thread(_cb_probe)
        body: dict[str, object] = {"status": "healthy", "service": SERVICE_NAME}
        if cb is False:
            body["status"] = "degraded"
            body["reason"] = "cb_unavailable"
        return body
