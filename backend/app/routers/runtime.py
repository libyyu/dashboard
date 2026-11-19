# -*- coding: utf-8 -*-
"""运行时聚合：把 launcher 注入的多服务状态收敛成前端单一契约。

前端不直连 model provider、不知道其端口；这里由 provider 目录推导 base_url，
避免浏览器跨域与端口硬编码，也符合「前后端通过契约解耦」。
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from pathlib import Path
from typing import cast

from fastapi import APIRouter
from starlette.responses import JSONResponse

from app.adapters.storage_factory import get_storage
from app.core.errors import error_response
from app.core.storage import DatabaseConfig
from app.routers.model import _default_provider
from app.runtime import log_policy, package_version, ports

router = APIRouter(prefix="/api/runtime", tags=["runtime"])

# 完整端点（契约见 contracts/api.json）：
#   GET  /api/runtime/status          BEST_EFFORT 轮询
#   GET  /api/runtime/services        BEST_EFFORT 轮询
#   GET  /api/runtime/storage         BEST_EFFORT，数据层自检
#   POST /api/runtime/auth/login      CONTROL，永不 shed

_CB_TIMEOUT = 2.0
_LOGIN_TIMEOUT = 5.0


def _request_json(
    url: str, timeout: float, *, method: str = "GET"
) -> dict[str, object] | None:
    try:
        request = urllib.request.Request(
            url,
            data=b"" if method == "POST" else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 - 可选服务不可达一律降级，不抛给前端
        return None


def _port_map() -> dict[str, object]:
    raw = ports.read_state().get("ports")
    return cast(dict[str, object], raw) if isinstance(raw, dict) else {}


def _read_services() -> list[dict[str, object]]:
    # 服务清单由装配层 launcher 通过 PLATEAU_SERVICES_JSON 注入（绝对路径）。
    # backend 不反向依赖 desktop 目录结构；未注入（如直接 python run.py）回退空清单。
    raw = os.environ.get("PLATEAU_SERVICES_JSON", "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    services = payload.get("services")
    return cast(list[dict[str, object]], services) if isinstance(services, list) else []


@router.get("/status")
async def runtime_status() -> dict[str, object]:
    provider = _default_provider()
    configured = provider is not None
    provider_name = str(provider["name"]) if provider else None
    cb_health = None
    auth = None
    if provider is not None:
        base_url = str(provider.get("base_url", "")).rstrip("/")
        cb_health = await asyncio.to_thread(
            _request_json, f"{base_url}/health", _CB_TIMEOUT
        )
        if provider.get("auth"):
            auth = await asyncio.to_thread(
                _request_json, f"{base_url}/auth/status", _CB_TIMEOUT
            )
    if cb_health is not None:
        level, reason = "healthy", ""
    elif configured:
        level, reason = "degraded", "cb_unavailable"
    else:
        level, reason = "standalone", "cb_not_configured"
    auth_status = str((auth or {}).get("status", "unknown"))
    return {
        "service": "plateau-dashboard",
        "version": package_version(),
        "profile": ports.profile_id(),
        "ports": _port_map(),
        "health": level,
        "reason": reason,
        "dashboard": {"status": "healthy", "ready": True},
        "cb": {
            "configured": configured,
            "available": cb_health is not None,
            "health": cb_health,
            "auth": auth,
            "login_status": auth_status,
            "provider": provider_name,
        },
        "logs_dir": str(log_policy.logs_dir()),
        "retention_days": log_policy.retention_days(),
    }


@router.get("/services")
async def runtime_services() -> dict[str, object]:
    port_map = _port_map()
    items: list[dict[str, object]] = []
    for svc in _read_services():
        name = str(svc.get("name", ""))
        items.append({
            "name": name,
            "required": bool(svc.get("required", False)),
            "port": port_map.get(name),
            "health_path": svc.get("health_path"),
            "ready_path": svc.get("ready_path"),
        })
    return {
        "profile": ports.profile_id(),
        "home": str(ports.home_dir()),
        "logs_dir": str(log_policy.logs_dir()),
        "services": items,
    }


@router.get("/storage", response_model=None)
async def runtime_storage() -> dict[str, object] | JSONResponse:
    """数据层自检：dialect / schema 版本 / 各集合行数。未装配时返回 NOT_CONFIGURED。"""
    if not DatabaseConfig.from_env().enabled:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    try:
        store = await asyncio.to_thread(get_storage)
        if store is None:
            return error_response("NOT_CONFIGURED", 503, "数据库未配置")
        health = await asyncio.to_thread(store.health)
    except Exception as exc:  # noqa: BLE001 - 存储异常统一转成契约错误形状
        return error_response("INTERNAL_ERROR", 500, f"存储不可用：{exc}")
    return {"profile": ports.profile_id(), **health}


@router.post("/auth/login", response_model=None)
async def runtime_login() -> dict[str, object] | JSONResponse:
    provider = _default_provider()
    if provider is None:
        return error_response("CB_UNAVAILABLE", 503, "模型服务未配置")
    if not provider.get("auth"):
        return error_response("NOT_CONFIGURED", 404, "模型服务不提供 auth")
    base_url = str(provider.get("base_url", "")).rstrip("/")
    result = await asyncio.to_thread(
        _request_json, f"{base_url}/auth/login", _LOGIN_TIMEOUT, method="POST"
    )
    if result is None:
        return error_response("CB_LOGIN_FAILED", 502, "模型服务登录失败")
    return result
