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
    "CB_UNAVAILABLE",          # 兼容别名（会话回放中可能出现，等价 PROVIDER_UNAVAILABLE）
    "CB_LOGIN_FAILED",         # 兼容别名（等价 PROVIDER_AUTH_FAILED）
    "PROVIDER_UNAVAILABLE",    # model provider 未启动 / 不可达 / 流中断
    "PROVIDER_AUTH_FAILED",    # provider 登录触发失败
    "PROVIDER_AUTH_REQUIRED",  # provider 需要登录（流内 error 事件码，前端给登录引导）
    "NOT_CONFIGURED",          # 功能端口未装配（如数据库未启用）
    "PLUGIN_NOT_FOUND",                # 插件未安装 / catalog 无此 id
    "PLUGIN_CHECKSUM_MISMATCH",        # 包体 sha256 校验失败
    "PLUGIN_CAPABILITY_UNSATISFIED",   # 插件能力依赖未满足
    "AGENT_NOT_READY",                 # 智能体能力未装配（无 model provider / 未登录）
    "AGENT_RUN_NOT_FOUND",             # 取消/查询的 run_id 不存在或已结束
    "AGENT_SESSION_NOT_FOUND",         # 会话不存在
    "FILE_NOT_FOUND",                  # 上传附件不存在
    "FILE_TOO_LARGE",                  # 上传附件超限
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
