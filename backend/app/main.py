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

from app.routers import agent, model, plugins, runtime
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
app.include_router(plugins.router)
app.include_router(model.router)
app.include_router(agent.router)


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


# ready 判据：核心路由已挂载；extended_routers 表示插件运行时（plugins.router）已挂载。
app.state.startup_phase = "routers_mounted"
app.state.core_routers_registered = True
app.state.extended_routers_registered = True


# 生产模式：托管前端构建产物（若已执行 npm run build）
class SPAStaticFiles(StaticFiles):
    """vue-router 深链回退 + 缓存策略：非 API 路径找不到文件时回 index.html。

    覆写 lookup_path 而不是截获 404 异常：静态文件命中/未命中由这一层决定，
    API 路径保持原生 404，不会被前端 index.html 吞掉。

    缓存策略（治 WebView 缓存旧 JS 的根因）：
    - index.html 是 SPA 壳，必须每次都向服务器校验，否则 WebView 缓存旧壳会继续
      引用旧哈希 JS，导致界面停留在旧版本；
    - assets/ 下的 Vite 构建产物文件名带内容哈希，可长期不可变缓存；
    - 其余文件（favicon、public 目录等）走 no-cache，避免误缓存。
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

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            # SPA 壳：强制重新校验，绝不让旧 index.html 被缓存复用
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif path.lstrip("/\\").replace("\\", "/").startswith("assets/"):
            # 内容哈希文件名，永久不可变缓存
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


_dist = resource_dir() / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(_dist), html=True), name="frontend")
