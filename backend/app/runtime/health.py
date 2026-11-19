# -*- coding: utf-8 -*-
"""三层探活：liveness / ready / health。

- liveness：只回答「进程还活着」，故意用 sync def 走 threadpool，event loop 卡住也能答
- ready：回答「核心路由已挂载」，未就绪返回 503 并带 phase
- health：综合状态；cb 已外置为 sidecar 插件，dashboard 无内置可选依赖，恒为 healthy

必须在业务路由之前注册，避免 liveness 被慢路由拖住。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

SERVICE_NAME = "plateau-dashboard"


def register_health_routes(app: FastAPI) -> None:
    @app.get("/health/liveness", tags=["health"])
    def liveness() -> dict[str, object]:
        return {"status": "alive", "service": SERVICE_NAME, "pid": os.getpid()}

    @app.get("/health/ready", tags=["health"], response_model=None)
    async def readiness(request: Request) -> dict[str, object] | JSONResponse:
        state = request.app.state
        core = bool(getattr(state, "core_routers_registered", False))
        ready = core
        body = {
            "status": "ready" if ready else "not_ready",
            "service": SERVICE_NAME,
            "phase": str(getattr(state, "startup_phase", "unknown")),
            "core_routers": core,
            "extended_routers": bool(getattr(state, "extended_routers_registered", False)),
        }
        return body if ready else JSONResponse(status_code=503, content=body)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, object]:
        return {"status": "healthy", "service": SERVICE_NAME}
