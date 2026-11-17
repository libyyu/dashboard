# -*- coding: utf-8 -*-
"""Plateau Dashboard 后端入口（FastAPI）。

- 提供 /api/* 接口
- 生产模式下托管前端构建产物 frontend/dist（若存在）
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import runtime
from app.runtime import package_version, resource_dir
from app.runtime.health import register_health_routes
from app.runtime.log_routes import register_log_routes
from app.runtime.request_log import RequestLogMiddleware
from app.runtime.traffic_lanes import TrafficLaneMiddleware

app = FastAPI(title="Plateau Dashboard", version=package_version())

# 车道最内层、CORS 中层、请求日志最外层：shed 的 503 与异常都能被记录并带上 x-request-id
app.add_middleware(TrafficLaneMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)

# 探活先于业务路由注册，liveness 不受慢接口影响
register_health_routes(app)
register_log_routes(app)
app.include_router(runtime.router)


@app.get("/api/ping")
def ping() -> dict:
    return {"ok": True, "service": "plateau-dashboard"}


# 兜底 500：异常细节由 RequestLogMiddleware 落盘，响应不泄露堆栈
@app.exception_handler(Exception)
async def unhandled_exception(request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "request_id": str(request.scope.get("plateau.request_id", "")),
            }
        },
    )


# ready 判据：核心路由已挂载。extended_routers 留给后续接入的扩展服务。
app.state.startup_phase = "routers_mounted"
app.state.core_routers_registered = True
app.state.extended_routers_registered = False


# 生产模式：托管前端构建产物（若已执行 npm run build）
class SPAStaticFiles(StaticFiles):
    """vue-router 深链回退：非 API 路径找不到文件时回 index.html。

    覆写 lookup_path 而不是截获 404 异常：静态文件命中/未命中由这一层决定，
    API 路径保持原生 404，不会被前端 index.html 吞掉。
    """

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        full_path, stat = super().lookup_path(path)
        if stat is not None:
            return full_path, stat
        # Windows 下 Starlette 会传反斜杠路径（如 api\xxx），先归一化再判前缀
        normalized = path.lstrip("/\\").replace("\\", "/")
        if normalized.startswith(("api/", "health", "auth/", "v1/", "docs", "openapi")):
            return full_path, None
        return super().lookup_path("index.html")


_dist = resource_dir() / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(_dist), html=True), name="frontend")
