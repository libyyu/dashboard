# -*- coding: utf-8 -*-
"""客户端插件运行时：本机已装插件清单 / 能力清单 / 安装 / 卸载 / registry 透传。

- 包体落 profile/plugins/{id}/{version}/，元数据落 plateau_kv 的 plugins collection
  （零 migration，复用现有 KV 表）
- registry 地址由 PLATEAU_REGISTRY_URL 注入，未注入回退 http://127.0.0.1:8700
- 安装/卸载只落盘 + 写 SQLite；sidecar 进程由 launcher 监听循环拉起（首版重启生效）
- 契约端点见 contracts/api.json；错误码 PLUGIN_* 见 core/errors.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.adapters.codebuddy_cli_provider import cli_env_check
from app.adapters.sqlite_storage import SqliteStorage
from app.core.errors import error_response
from app.core.storage import DatabaseConfig
from app.runtime import package_version, ports

router = APIRouter(prefix="/api", tags=["plugins"])

_PLUGINS_COLLECTION = "plugins"
_RUNTIMES_COLLECTION = "runtimes"
# 内核能力：dashboard 内核直接实现，插件可 requires。
_KERNEL_CAPABILITIES = ("search", "graph", "storage.kv")
# 插件能力：由已装插件 provides 提供（当前仅 cb 的 model），其它插件可 requires。
_PLUGIN_CAPABILITIES = ("model",)
# requires 白名单 = 内核能力 ∪ 插件能力。
_CAPABILITY_WHITELIST = _KERNEL_CAPABILITIES + _PLUGIN_CAPABILITIES
# provides 白名单 = 插件能力（只有 sidecar 能声明）。
_PROVIDES_WHITELIST = _PLUGIN_CAPABILITIES
# 插件形态：frontend 前端页面 / sidecar 常驻 HTTP / cli 本地 CLI 文件（subprocess 直调，不常驻）
_KINDS = ("frontend", "sidecar", "cli")
_DEFAULT_REGISTRY = "http://127.0.0.1:8700"
_REGISTRY_TIMEOUT = 10.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_storage: SqliteStorage | None = None
_storage_lock = threading.Lock()

# 安装进度：{plugin_id: {stage, message, current, total, done, error}}，
# 供前端在 install 进行中轮询（GET /api/plugins/install/progress）。
_install_progress: dict[str, dict[str, object]] = {}
_progress_lock = threading.Lock()


def _set_progress(
    plugin_id: str,
    stage: str,
    message: str,
    current: int = 0,
    total: int = 0,
    done: bool = False,
    error: str = "",
) -> None:
    with _progress_lock:
        _install_progress[plugin_id] = {
            "stage": stage,
            "message": message,
            "current": current,
            "total": total,
            "done": done,
            "error": error,
        }


class _PluginError(Exception):
    """插件业务错误：code/status/message 与统一错误契约对齐。"""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def _get_storage() -> SqliteStorage | None:
    global _storage
    config = DatabaseConfig.from_env()
    if not config.enabled:
        return None
    if config.dialect != "sqlite":
        raise ValueError(f"当前只装配了 sqlite 适配器：{config.dialect}")
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                _storage = SqliteStorage(config)
    return _storage


def _registry_url() -> str:
    raw = (os.environ.get("PLATEAU_REGISTRY_URL") or "").strip().rstrip("/")
    return raw or _DEFAULT_REGISTRY


def _plugins_root() -> Path:
    return ports.home_dir() / "profiles" / ports.profile_id() / "plugins"


def _plugin_dir(plugin_id: str, version: str) -> Path:
    return _plugins_root() / plugin_id / version


def _runtimes_root() -> Path:
    return ports.home_dir() / "profiles" / ports.profile_id() / "runtimes"


def _runtime_dir(runtime_id: str, version: str) -> Path:
    return _runtimes_root() / runtime_id / version


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version_tuple(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(raw).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def _request_bytes(url: str, timeout: float) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except Exception:  # noqa: BLE001 - registry 不可达一律降级
        return None


def _request_json(url: str, timeout: float) -> dict[str, object] | None:
    raw = _request_bytes(url, timeout)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
        return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _platform_id() -> str:
    """当前平台 {os}-{arch} 标识，用于匹配 runtime.json 的 platforms 键。"""
    system = platform.system().lower()
    if system == "windows":
        os_name = "win"
    elif system == "darwin":
        os_name = "darwin"
    else:
        os_name = "linux"
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = machine
    return f"{os_name}-{arch}"


def _parse_runtime_spec(spec: object) -> tuple[str, str]:
    """解析运行时依赖 'id' 或 'id@version'，返回 (id, version)。"""
    text = str(spec).strip()
    if "@" in text:
        runtime_id, version = text.split("@", 1)
        return runtime_id.strip(), version.strip()
    return text, ""


def _download_to_file(url: str, dest: Path, on_progress=None) -> None:
    """流式下载到本地文件（node 发行包体积较大，避免一次性读入内存）。

    on_progress(read_bytes, total_bytes)：total 取自 Content-Length，未知时为 0。
    """
    try:
        with urllib.request.urlopen(url, timeout=_REGISTRY_TIMEOUT) as response:
            raw_length = response.headers.get("Content-Length") or ""
            total = int(raw_length) if raw_length.isdigit() else 0
            read = 0
            with dest.open("wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
                    read += len(chunk)
                    if on_progress is not None:
                        on_progress(read, total)
    except Exception as exc:  # noqa: BLE001 - 下载失败统一转业务错误
        raise _PluginError("PLUGIN_NOT_FOUND", 404, f"运行时下载失败：{url}（{exc}）") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive(archive: Path, target_dir: Path, archive_format: str) -> None:
    """解压 zip/tar 运行时包，并把顶层目录内容平铺到 target_dir。

    官方 node 包解压后是单层目录（如 node-v22.23.5-win-x64/），这里把其内容平铺，
    使 target_dir 下直接就是入口（node.exe / node，名见 runtime.json 的 node_bin）。
    """
    extract_dir = target_dir.parent / f"{target_dir.name}-{os.getpid()}.extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        if archive_format == "zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(extract_dir)
        entries = [p for p in extract_dir.iterdir()]
        src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_dir
        for item in src.iterdir():
            dst = target_dir / item.name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    dst.unlink(missing_ok=True)
            shutil.move(str(item), str(dst))
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _runtime_ready(target_dir: Path, node_bin: str) -> bool:
    """运行时是否已可用：入口文件存在，且实际运行 `--version` 成功。

    比「文件存在」更可靠：能发现入口被单独删除、缺 DLL、架构不对等损坏情况；
    不再依赖 .complete 安装标记（标记只代表「当时装完」，不代表「现在可用」）。
    """
    bin_path = target_dir / node_bin
    if not bin_path.is_file():
        return False
    try:
        proc = subprocess.run(
            [str(bin_path), "--version"],
            capture_output=True, text=True, timeout=15,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - 启动失败即视为不可用
        return False


def _ensure_runtime(store: SqliteStorage, spec: object, plugin_id: str = "") -> None:
    """确保共享运行时已装：同版本已可用则跳过；否则从 registry 读元数据，按当前平台下载解压。"""
    runtime_id, version = _parse_runtime_spec(spec)
    if not runtime_id:
        raise _PluginError("PLUGIN_CAPABILITY_UNSATISFIED", 422, "runtime 依赖格式非法")
    if not version:
        raise _PluginError(
            "PLUGIN_CAPABILITY_UNSATISFIED", 422,
            f"运行时 {runtime_id} 需显式版本，形如 {runtime_id}@22.23.5",
        )

    target_dir = _runtime_dir(runtime_id, version)

    registry = _registry_url()
    meta = _request_json(
        f"{registry}/runtimes/{runtime_id}/{version}/runtime.json", _REGISTRY_TIMEOUT
    )
    if meta is None:
        raise _PluginError("PLUGIN_NOT_FOUND", 404, f"运行时不存在：{runtime_id}/{version}")

    platforms = meta.get("platforms")
    block = platforms.get(_platform_id()) if isinstance(platforms, dict) else None
    if not isinstance(block, dict):
        raise _PluginError(
            "PLUGIN_CAPABILITY_UNSATISFIED", 422,
            f"运行时 {runtime_id}/{version} 不支持当前平台 {_platform_id()}",
        )
    node_bin = str(block.get("node_bin") or "")
    download = block.get("download")
    download = download if isinstance(download, dict) else {}
    url = str(download.get("url") or "")
    expected_sha = str(download.get("sha256") or "")
    if not node_bin or not url:
        raise _PluginError(
            "PLUGIN_CAPABILITY_UNSATISFIED", 422,
            f"运行时 {runtime_id}/{version} 元数据缺 node_bin / download.url",
        )

    # 已装判断：入口文件在 + 实际可运行；否则重新下载（覆盖半成品/被删/损坏）
    if _runtime_ready(target_dir, node_bin):
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    # 下载 URL 扩展名决定归档格式（.zip → zip；.tar.gz/.tar.xz 等 → tar，由 tarfile 自动解压）
    archive_format = "zip" if url.lower().rstrip("/").endswith(".zip") else "tar"
    tmp_suffix = ".zip" if archive_format == "zip" else ".tar"
    tmp_archive = target_dir.parent / f"{runtime_id}-{version}-{os.getpid()}{tmp_suffix}"

    def _runtime_progress(read: int, total: int) -> None:
        if not plugin_id:
            return
        msg = f"下载共享运行时 {runtime_id} {read // (1 << 20)}MB"
        if total:
            msg += f" / {total // (1 << 20)}MB"
        _set_progress(plugin_id, "runtime", msg, read, total)

    try:
        _download_to_file(url, tmp_archive, _runtime_progress)
        if _SHA256_RE.match(expected_sha) and _sha256_file(tmp_archive) != expected_sha:
            raise _PluginError(
                "PLUGIN_CHECKSUM_MISMATCH", 422,
                f"运行时 {runtime_id}/{version} sha256 校验失败",
            )
        _extract_archive(tmp_archive, target_dir, archive_format)
        if not (target_dir / node_bin).is_file():
            raise _PluginError(
                "PLUGIN_CAPABILITY_UNSATISFIED", 422,
                f"运行时 {runtime_id}/{version} 解压后缺少入口 {node_bin}",
            )
    finally:
        tmp_archive.unlink(missing_ok=True)

    store.put(_RUNTIMES_COLLECTION, runtime_id, {
        "version": version,
        "node_bin": node_bin,
        "path": str(target_dir),
        "installed_at": _utc_now(),
    })


def _validate_manifest(manifest: dict[str, object]) -> list[str]:
    problems: list[str] = []
    if not str(manifest.get("id") or "").strip():
        problems.append("id 必填")
    if manifest.get("kind") not in _KINDS:
        problems.append(f"kind 必须是 {sorted(_KINDS)}")
    requires = manifest.get("requires")
    if isinstance(requires, list):
        for cap in requires:
            if cap not in _CAPABILITY_WHITELIST:
                problems.append(f"requires 含未定义能力 {cap!r}，白名单：{sorted(_CAPABILITY_WHITELIST)}")
    provides = manifest.get("provides")
    if provides and provides not in _PROVIDES_WHITELIST:
        problems.append(f"provides 含未定义能力 {provides!r}，白名单：{sorted(_PROVIDES_WHITELIST)}")
    runtimes = manifest.get("runtimes")
    if runtimes is not None:
        if (not isinstance(runtimes, list)
                or not all(isinstance(r, str) and r.strip() for r in runtimes)):
            problems.append("runtimes 必须是非空字符串数组（形如 'node' 或 'node@22.22.2'）")
    problems.extend(_validate_model_block(manifest, provides))
    return problems


def _validate_model_block(manifest: dict[str, object], provides: object) -> list[str]:
    """provides=model 的 sidecar 可用 model 块声明协议与登录方式；形状不对则拒绝安装。

    - protocols：字符串列表，非空（未声明时运行时兜底 openai-completions）
    - default_protocol：必须是 protocols 之一
    - auth：api-key | login | none
    """
    model_block = manifest.get("model")
    if model_block is None:
        return []
    if provides != "model":
        return ["model 块仅允许 provides=model 的插件声明"]
    if not isinstance(model_block, dict):
        return ["model 块必须是对象"]
    problems: list[str] = []
    protocols = model_block.get("protocols")
    if protocols is not None:
        if (not isinstance(protocols, list)
                or not protocols
                or not all(isinstance(p, str) and p.strip() for p in protocols)):
            problems.append("model.protocols 必须是非空字符串数组")
    default_protocol = model_block.get("default_protocol")
    if isinstance(default_protocol, str) and default_protocol.strip():
        if not isinstance(protocols, list) or default_protocol not in protocols:
            problems.append("model.default_protocol 必须在 model.protocols 之内")
    auth = model_block.get("auth")
    if auth is not None and auth not in ("api-key", "login", "none"):
        problems.append("model.auth 必须是 api-key | login | none")
    return problems


def _provided_capabilities(store: SqliteStorage) -> set[str]:
    """已装且 enabled 的插件对外提供的能力集合。"""
    provided: set[str] = set()
    for item in store.query(_PLUGINS_COLLECTION, limit=1000):
        if not item.get("enabled", True):
            continue
        provides = item.get("provides")
        if isinstance(provides, str) and provides:
            provided.add(provides)
    return provided


def _missing_deps(store: SqliteStorage, requires: list[object]) -> list[str]:
    """requires 引用插件能力（model）但本机尚无 enabled 插件提供的能力。"""
    plugin_reqs = [str(c) for c in requires if c in _PLUGIN_CAPABILITIES]
    provided = _provided_capabilities(store)
    return [c for c in plugin_reqs if c not in provided]


def _read_manifest_meta(plugin_id: str, version: str) -> dict[str, object]:
    path = _plugin_dir(plugin_id, version) / "plugin.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remove_plugin_dir(plugin_id: str) -> None:
    root = _plugins_root() / plugin_id
    shutil.rmtree(root, ignore_errors=True)


def _remove_other_versions(plugin_id: str, keep_version: str) -> None:
    """升级后清理同插件旧版本目录，避免遗留孤儿文件（provider 只按最高版本解析）。"""
    root = _plugins_root() / plugin_id
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name != keep_version:
            shutil.rmtree(child, ignore_errors=True)


def _do_install(store: SqliteStorage, plugin_id: str, version: str) -> dict[str, object]:
    """安装外壳：失败时把进度标记为 done+error，保证前端轮询能收到终态。"""
    try:
        return _do_install_inner(store, plugin_id, version)
    except _PluginError as exc:
        _set_progress(plugin_id, "failed", exc.message, done=True, error=exc.message)
        raise


def _do_install_inner(store: SqliteStorage, plugin_id: str, version: str) -> dict[str, object]:
    """同步执行安装（跑在线程池里）：拉 manifest → 下载 → sha256 校验 → 落盘 → 写 SQLite。"""
    registry = _registry_url()
    _set_progress(plugin_id, "resolve", "解析插件版本…")

    # 1. 版本未指定时从 catalog 解析 latest
    if not version:
        catalog = _request_json(f"{registry}/plugins/catalog.json", _REGISTRY_TIMEOUT)
        if catalog:
            for entry in catalog.get("plugins") or []:
                if isinstance(entry, dict) and entry.get("id") == plugin_id:
                    version = str(entry.get("latest_version") or "")
                    break
    if not version:
        raise _PluginError("PLUGIN_NOT_FOUND", 404, f"插件不存在或未指定版本：{plugin_id}")

    # 2. 拉 manifest
    manifest = _request_json(
        f"{registry}/plugins/{plugin_id}/{version}/plugin.json", _REGISTRY_TIMEOUT
    )
    if manifest is None or not str(manifest.get("id") or "").strip():
        raise _PluginError("PLUGIN_NOT_FOUND", 404, f"插件不存在：{plugin_id}/{version}")

    # 3. 校验 manifest
    problems = _validate_manifest(manifest)
    if problems:
        raise _PluginError("PLUGIN_CAPABILITY_UNSATISFIED", 422, "；".join(problems))
    _set_progress(plugin_id, "manifest", "校验插件清单…")
    # 3.4 运行时依赖：先确保共享运行时已装（node 等），再装插件本体
    runtimes = manifest.get("runtimes")
    runtimes = runtimes if isinstance(runtimes, list) else []
    for runtime_spec in runtimes:
        _ensure_runtime(store, runtime_spec, plugin_id)
    # 3.5 依赖满足检查：requires 里的插件能力（model）需已有 enabled 插件提供
    requires = manifest.get("requires")
    requires = requires if isinstance(requires, list) else []
    missing = _missing_deps(store, requires)
    if missing:
        raise _PluginError(
            "PLUGIN_CAPABILITY_UNSATISFIED", 422,
            f"缺少能力依赖 {sorted(missing)}：请先安装提供该能力的插件",
        )
    if _version_tuple(str(manifest.get("requires_core") or "0")) > _version_tuple(package_version()):
        raise _PluginError(
            "PLUGIN_CAPABILITY_UNSATISFIED", 422,
            f"需要更高内核版本：{manifest.get('requires_core')}（当前 {package_version()}）",
        )

    # 4. 下载文件 + sha256 校验 + 落盘
    dest = _plugin_dir(plugin_id, version)
    dest.mkdir(parents=True, exist_ok=True)
    files = manifest.get("files")
    if isinstance(files, list):
        total_files = len(files)
        for idx, file_entry in enumerate(files, 1):
            _set_progress(
                plugin_id, "files", f"下载插件文件 {idx}/{total_files}", idx, total_files
            )
            if not isinstance(file_entry, dict):
                continue
            rel = str(file_entry.get("path") or "").replace("\\", "/").lstrip("/")
            if not rel:
                continue
            target = (dest / rel).resolve()
            if target != dest and dest not in target.parents:
                raise _PluginError("INTERNAL_ERROR", 500, f"非法文件路径：{rel}")
            raw = _request_bytes(f"{registry}/plugins/{plugin_id}/{version}/{rel}", _REGISTRY_TIMEOUT)
            if raw is None:
                raise _PluginError("PLUGIN_NOT_FOUND", 404, f"文件下载失败：{rel}")
            expected = str(file_entry.get("sha256") or "")
            if _SHA256_RE.match(expected):
                actual = hashlib.sha256(raw).hexdigest()
                if actual != expected:
                    raise _PluginError("PLUGIN_CHECKSUM_MISMATCH", 422, f"sha256 校验失败：{rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    # 5. 写 manifest 到磁盘
    manifest_path = dest / "plugin.json"
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)

    # 6. 写 SQLite 元数据
    requires = manifest.get("requires")
    requires = requires if isinstance(requires, list) else []
    store.put(_PLUGINS_COLLECTION, plugin_id, {
        "version": version,
        "kind": manifest.get("kind"),
        "requires": requires,
        "provides": manifest.get("provides"),
        "enabled": True,
        "installed_at": _utc_now(),
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest_path": str(manifest_path),
    })

    _remove_other_versions(plugin_id, version)
    _set_progress(plugin_id, "done", "安装完成", done=True)
    return {"ok": True, "id": plugin_id, "version": version, "restart_required": True}


async def _json_body(request: Request) -> dict[str, object]:
    try:
        body = await request.json()
        return cast(dict[str, object], body) if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001 - 空/非 JSON 体一律当空
        return {}


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.get("/plugins", response_model=None)
async def list_plugins() -> dict[str, object] | JSONResponse:
    store = await asyncio.to_thread(_get_storage)
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    items = await asyncio.to_thread(store.query, _PLUGINS_COLLECTION, limit=1000)
    plugins: list[dict[str, object]] = []
    for item in items:
        plugin_id = str(item.get("key") or "")
        version = str(item.get("version") or "")
        manifest_meta = _read_manifest_meta(plugin_id, version)
        plugins.append({
            "id": plugin_id,
            "version": item.get("version"),
            "kind": item.get("kind"),
            "name": manifest_meta.get("name"),
            "requires": item.get("requires") or [],
            "provides": item.get("provides"),
            "enabled": item.get("enabled", True),
            "installed_at": item.get("installed_at"),
            "menu": manifest_meta.get("menu"),
            "routes": manifest_meta.get("routes"),
        })
    return {"plugins": plugins}


def _model_capability_satisfied(store: SqliteStorage | None) -> bool:
    """model 能力是否就绪：存在 enabled 且 provides=model 的插件，且端口已写入 runtime_state。"""
    if store is None:
        return False
    state_ports = ports.read_state().get("ports")
    if not isinstance(state_ports, dict):
        state_ports = {}
    for item in store.query(_PLUGINS_COLLECTION, limit=1000):
        if item.get("provides") == "model" and item.get("enabled", True):
            plugin_id = str(item.get("key") or "")
            port = state_ports.get(f"plugin-{plugin_id}")
            if isinstance(port, int) and port > 0:
                return True
    return False


@router.get("/plugins/capabilities")
async def plugin_capabilities() -> dict[str, object]:
    store = await asyncio.to_thread(_get_storage)
    kv_satisfied = store is not None
    model_satisfied = await asyncio.to_thread(_model_capability_satisfied, store)
    return {"capabilities": [
        {"id": "search", "name": "全文检索", "provider": "kernel", "satisfied": False},
        {"id": "graph", "name": "玩法图谱", "provider": "kernel", "satisfied": False},
        {"id": "storage.kv", "name": "KV 存储", "provider": "kernel", "satisfied": kv_satisfied},
        {"id": "model", "name": "大模型服务", "provider": "sidecar", "satisfied": model_satisfied},
    ]}


@router.get("/plugins/cli-env/check")
async def cli_env_check_endpoint() -> dict[str, object]:
    """codebuddy-cli 运行环境检查：插件商店打开时自动调用，返回逐项状态与修复动作。"""
    return await asyncio.to_thread(cli_env_check)


@router.post("/plugins/install", response_model=None)
async def install_plugin(request: Request) -> dict[str, object] | JSONResponse:
    body = await _json_body(request)
    plugin_id = str(body.get("id") or "").strip()
    version = str(body.get("version") or "").strip()
    if not plugin_id:
        return error_response("PLUGIN_NOT_FOUND", 400, "缺少 id")
    store = await asyncio.to_thread(_get_storage)
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    try:
        result = await asyncio.to_thread(_do_install, store, plugin_id, version)
    except _PluginError as exc:
        return error_response(exc.code, exc.status, exc.message)
    return result


@router.get("/plugins/install/progress")
async def install_progress(request: Request) -> dict[str, object]:
    plugin_id = str(request.query_params.get("id") or "").strip()
    if not plugin_id:
        return {"active": False}
    with _progress_lock:
        state = _install_progress.get(plugin_id)
        if state is None:
            return {"active": False}
        return dict(state)


@router.post("/plugins/uninstall", response_model=None)
async def uninstall_plugin(request: Request) -> dict[str, object] | JSONResponse:
    body = await _json_body(request)
    plugin_id = str(body.get("id") or "").strip()
    if not plugin_id:
        return error_response("PLUGIN_NOT_FOUND", 400, "缺少 id")
    store = await asyncio.to_thread(_get_storage)
    if store is None:
        return error_response("NOT_CONFIGURED", 503, "数据库未配置（DATABASE_URL 为空）")
    record = await asyncio.to_thread(store.get, _PLUGINS_COLLECTION, plugin_id)
    if record is None:
        return error_response("PLUGIN_NOT_FOUND", 404, f"插件未安装：{plugin_id}")
    await asyncio.to_thread(_remove_plugin_dir, plugin_id)
    await asyncio.to_thread(store.delete, _PLUGINS_COLLECTION, plugin_id)
    return {"ok": True, "id": plugin_id, "restart_required": True}


@router.get("/plugins/registry")
async def plugin_registry() -> dict[str, object]:
    catalog = await asyncio.to_thread(
        _request_json, f"{_registry_url()}/plugins/catalog.json", _REGISTRY_TIMEOUT
    )
    if catalog is None:
        return {"schema": 1, "plugins": [], "reachable": False}
    catalog["reachable"] = True
    return catalog
