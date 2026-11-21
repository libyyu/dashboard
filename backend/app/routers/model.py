# -*- coding: utf-8 -*-
"""模型 provider 转发层：把稳定内核路径透明转发到 OpenAI 兼容上游。

- provider 无关转发（透传 + SSE 透传），不翻译语义、不做智能体编排
- provider 目录两源合并（只读视图，不互相写）：
  ① 注册表 KV（model_providers 集合）——手动登记的标准 OpenAI 兼容供应商
  ② 插件扫描——已装 provides=model 的 sidecar，端口由 launcher 写 runtime_state，
    manifest 的 model 块声明协议（protocols/default_protocol）与登录方式（auth）
- api_type：adapter 分派键。openai-completions=标准 OpenAI 面；agent-events=cb
  私有统一事件流（无损）；其余（如 cli-*）由未来 adapter 注册
- api_key 只存在于注册表条目与进程内转发头，绝不进 /model/providers 响应
- 车道：providers/models/auth 归 CONTROL，chat/completions 归 STREAM（见 traffic_lanes）
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from typing import cast
from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, StreamingResponse

from app.core.errors import error_response
from app.adapters.codebuddy_cli_provider import (
    CodeBuddyCliProvider,
    cli_supported_models,
    has_usable_auth,
    save_api_key,
)
from app.adapters.storage_factory import get_storage
from app.runtime import ports

router = APIRouter(prefix="/api", tags=["model"])

_MODELS_TIMEOUT = 10.0
_AUTH_TIMEOUT = 5.0
_STREAM_TIMEOUT = 1800.0

_REGISTRY_COLLECTION = "model_providers"
_KNOWN_AUTH_MODES = ("api-key", "login", "none")


# --------------------------------------------------------------------------- #
# provider 目录
# --------------------------------------------------------------------------- #
def _plugins_root() -> Path:
    return ports.home_dir() / "profiles" / ports.profile_id() / "plugins"


def _registry_providers() -> list[dict[str, object]]:
    """注册表 KV：手动登记的标准 OpenAI 兼容供应商（智谱 / DeepSeek / Ollama …）。"""
    storage = get_storage()
    if storage is None:
        return []
    out: list[dict[str, object]] = []
    for item in storage.query(_REGISTRY_COLLECTION, limit=100):
        name = str(item.get("key") or "").strip()
        base = str(item.get("base_url") or "").strip().rstrip("/")
        if not name or not base:
            continue
        out.append({
            "name": name,
            "display_name": str(item.get("display_name") or name),
            "base_url": base,
            "source": "registry",
            "api_type": str(item.get("api_type") or "openai-completions"),
            "auth": str(item.get("auth") or "api-key"),
            "api_key": str(item.get("api_key") or ""),
        })
    return out


def _sidecar_providers() -> list[dict[str, object]]:
    """插件扫描：provides=model 的 sidecar；manifest model 块声明协议与登录方式。

    未声明 model 块的旧插件兜底为 openai-completions + login（sidecar 最低要求
    就是 OpenAI 面；cb 类 sidecar 均由自身管理登录态）。
    """
    result: list[dict[str, object]] = []
    root = _plugins_root()
    if not root.is_dir():
        return result
    state_ports = ports.read_state().get("ports")
    if not isinstance(state_ports, dict):
        state_ports = {}
    for plugin_dir in sorted(root.iterdir()):
        if not plugin_dir.is_dir():
            continue
        for version_dir in sorted(plugin_dir.iterdir()):
            manifest_path = version_dir / "plugin.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(manifest, dict):
                continue
            if manifest.get("kind") != "sidecar" or manifest.get("provides") != "model":
                continue
            plugin_id = str(manifest.get("id") or plugin_dir.name)
            port = state_ports.get(f"plugin-{plugin_id}")
            if not isinstance(port, int) or port <= 0:
                continue
            model_block = manifest.get("model")
            model_block = model_block if isinstance(model_block, dict) else {}
            protocols = [
                str(p) for p in (model_block.get("protocols") or [])
                if isinstance(p, str) and p.strip()
            ] or ["openai-completions"]
            default_protocol = str(model_block.get("default_protocol") or protocols[0])
            if default_protocol not in protocols:
                default_protocol = protocols[0]
            auth = str(model_block.get("auth") or "login")
            if auth not in _KNOWN_AUTH_MODES:
                auth = "login"
            result.append({
                "name": plugin_id,
                "display_name": str(manifest.get("name") or plugin_id),
                "base_url": f"http://127.0.0.1:{port}",
                "source": "sidecar",
                "api_type": default_protocol,
                "protocols": protocols,
                "auth": auth,
            })
    return result


def _model_providers() -> list[dict[str, object]]:
    """provider 目录（有序）：sidecar 插件 → 注册表；同名以插件为准。"""
    providers: list[dict[str, object]] = []
    seen: set[str] = set()
    for provider in _sidecar_providers():
        providers.append(provider)
        seen.add(str(provider["name"]))
    for entry in _registry_providers():
        if str(entry["name"]) not in seen:
            providers.append(entry)
    # 本地 CodeBuddy CLI（cli 插件，非 HTTP provider）：插件 + 共享 node 都就绪才暴露，避免前端选中后跑不通
    if CodeBuddyCliProvider.available() and "codebuddy-cli" not in seen:
        providers.append({
            "name": "codebuddy-cli",
            "display_name": "CodeBuddy CLI（本地）",
            "base_url": "",
            "source": "plugin",
            "api_type": "cli",
            "auth": "login",
        })
    return providers


def _find_provider(name: str) -> dict[str, object] | None:
    for provider in _model_providers():
        if provider.get("name") == name:
            return provider
    return None


def _default_provider() -> dict[str, object] | None:
    providers = _model_providers()
    return providers[0] if providers else None


def _public_providers() -> list[dict[str, object]]:
    """/model/providers 响应：去掉 api_key 等内部凭证字段。"""
    out: list[dict[str, object]] = []
    for provider in _model_providers():
        public = {k: v for k, v in provider.items() if k != "api_key"}
        out.append(public)
    return out


def _auth_headers(provider: dict[str, object]) -> dict[str, str]:
    """注册表条目的 api_key 以 Bearer 注入；sidecar 凭证由插件自己管，不注入。"""
    key = str(provider.get("api_key") or "").strip()
    if key and provider.get("source") == "registry":
        return {"Authorization": f"Bearer {key}"}
    return {}


def _request_json(
    url: str, timeout: float, *, method: str = "GET", data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object] | None:
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    try:
        request = urllib.request.Request(url, data=data, method=method, headers=merged)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 - provider 不可达一律降级
        return None


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.get("/model/providers")
async def model_providers() -> dict[str, object]:
    providers = _public_providers()
    default = providers[0].get("name") if providers else None
    return {"providers": providers, "default": default}


@router.get("/model/{provider}/models", response_model=None)
async def model_list(provider: str) -> dict[str, object] | JSONResponse:
    info = _find_provider(provider)
    if info is None:
        return error_response("PLUGIN_NOT_FOUND", 404, f"未知 model provider：{provider}")
    if info.get("api_type") == "cli":
        # 本地 CLI 无 /v1/models 面：动态解析 --help 的支持清单（带进程级缓存）
        models = await asyncio.to_thread(cli_supported_models)
        return {"object": "list", "data": [
            {"id": m, "object": "model", "owned_by": "codebuddy-cli"} for m in models
        ]}
    result = await asyncio.to_thread(
        _request_json, f"{info['base_url']}/v1/models", _MODELS_TIMEOUT,
        headers=_auth_headers(info),
    )
    if result is None:
        return error_response("PROVIDER_UNAVAILABLE", 503, f"provider {provider} 不可达")
    return result


@router.get("/model/{provider}/models/{model_id}", response_model=None)
async def model_detail(provider: str, model_id: str) -> dict[str, object] | JSONResponse:
    info = _find_provider(provider)
    if info is None:
        return error_response("PLUGIN_NOT_FOUND", 404, f"未知 model provider：{provider}")
    if info.get("api_type") == "cli":
        models = await asyncio.to_thread(cli_supported_models)
        if model_id not in models:
            return error_response("MODEL_NOT_FOUND", 404, f"未知模型：{model_id}")
        return {"id": model_id, "object": "model", "owned_by": "codebuddy-cli"}
    result = await asyncio.to_thread(
        _request_json, f"{info['base_url']}/v1/models/{model_id}", _MODELS_TIMEOUT,
        headers=_auth_headers(info),
    )
    if result is None:
        return error_response("PROVIDER_UNAVAILABLE", 503, f"provider {provider} 不可达")
    return result


@router.post("/model/{provider}/chat/completions", response_model=None)
async def model_chat(provider: str, request: Request) -> StreamingResponse | JSONResponse:
    info = _find_provider(provider)
    if info is None:
        return error_response("PLUGIN_NOT_FOUND", 404, f"未知 model provider：{provider}")
    body = await request.body()
    upstream = f"{info['base_url']}/v1/chat/completions"

    def stream() -> object:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        headers.update(_auth_headers(info))
        req = urllib.request.Request(upstream, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_STREAM_TIMEOUT) as response:
                for chunk in iter(lambda: response.read(8192), b""):
                    yield chunk
        except Exception as exc:  # noqa: BLE001 - 上游中断时返回契约错误事件
            yield json.dumps(
                {"error": {"code": "PROVIDER_UNAVAILABLE", "message": f"provider {provider} 不可达：{exc}"}},
                ensure_ascii=False,
            ).encode("utf-8")

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/model/{provider}/auth/status", response_model=None)
async def model_auth_status(provider: str) -> dict[str, object] | JSONResponse:
    info = _find_provider(provider)
    if info is None:
        return error_response("PLUGIN_NOT_FOUND", 404, f"未知 model provider：{provider}")
    if info.get("auth") != "login":
        return error_response("NOT_CONFIGURED", 404, f"provider {provider} 不提供 auth")
    if info.get("api_type") == "cli":
        # 本地 CLI：已存 API key，或全局公开 codebuddy 就位（自带登录态）即视为 ready
        return {"status": "ready" if has_usable_auth() else "pending"}
    result = await asyncio.to_thread(
        _request_json, f"{info['base_url']}/auth/status", _AUTH_TIMEOUT,
        headers=_auth_headers(info),
    )
    if result is None:
        return error_response("PROVIDER_UNAVAILABLE", 503, f"provider {provider} 不可达")
    return result


@router.post("/model/{provider}/auth/login", response_model=None)
async def model_auth_login(provider: str, request: Request) -> dict[str, object] | JSONResponse:
    info = _find_provider(provider)
    if info is None:
        return error_response("PLUGIN_NOT_FOUND", 404, f"未知 model provider：{provider}")
    if info.get("auth") != "login":
        return error_response("NOT_CONFIGURED", 404, f"provider {provider} 不提供 auth")
    if info.get("api_type") == "cli":
        # 本地 CLI：接收前端填入的 API key，落库供后续会话复用
        api_key = ""
        try:
            body = json.loads((await request.body()).decode("utf-8"))
            if isinstance(body, dict):
                api_key = str(body.get("api_key") or "").strip()
        except Exception:  # noqa: BLE001 - body 缺失/非 JSON 时按空处理
            api_key = ""
        if not api_key:
            return error_response("INVALID_ARGUMENT", 422, "缺少 api_key")
        save_api_key(api_key)
        return {"started": True, "status": "ready"}
    result = await asyncio.to_thread(
        _request_json, f"{info['base_url']}/auth/login", _AUTH_TIMEOUT,
        method="POST", headers=_auth_headers(info),
    )
    if result is None:
        return error_response("PROVIDER_AUTH_FAILED", 502, f"provider {provider} 登录失败")
    return result
