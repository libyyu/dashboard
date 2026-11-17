# -*- coding: utf-8 -*-
"""统一错误契约：``{"error":{"code","message","request_id"?}}``。

前后端只认这个形状，不认 HTTPException 的 detail、不认框架异常文本。
前端 http.ts 与后端所有新增路由共用这里的码表语义。
"""
from __future__ import annotations

from starlette.responses import JSONResponse

ERROR_CODES = frozenset({
    "GATEWAY_OVERLOAD_SHED",   # BEST_EFFORT 车道过载，按 retry-after 退避，不是故障
    "INTERNAL_ERROR",          # 兜底 500，带 request_id 供日志反查
    "INVALID_SOURCE",          # 日志 source 路径守卫
    "CB_UNAVAILABLE",          # 可选服务 cb 未启动 / 不可达
    "CB_LOGIN_FAILED",         # cb 登录触发失败
    "NOT_CONFIGURED",          # 功能端口未装配（如数据库未启用）
})


def error_response(
    code: str,
    status: int,
    message: str,
    *,
    request_id: str = "",
    **extra: object,
) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message}
    if request_id:
        body["request_id"] = request_id
    body.update(extra)
    return JSONResponse(status_code=status, content={"error": body})
