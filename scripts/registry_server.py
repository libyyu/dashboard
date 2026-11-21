# -*- coding: utf-8 -*-
"""plateau-registry 开发态服务：静态托管插件仓库目录 + 自动生成 catalog.json。

用法：
  python scripts/registry_server.py [--port 8700] [--root registry]

目录结构（--root 下，默认项目根 registry/）：
  catalog.json                             # 可手工维护；缺省时按 manifest 扫描生成
  plugins/{id}/{version}/plugin.json       # manifest
  plugins/{id}/{version}/{files...}        # 包体文件
  runtimes/{id}/{version}/runtime.json     # 运行时元数据（官方下载 URL + sha256，包体不落 registry）

端点：
  GET /healthz
  GET /plugins/catalog.json
  GET /plugins/{id}/{version}/plugin.json
  GET /plugins/{id}/{version}/{file}
  GET /runtimes/{id}/{version}/runtime.json

定位：cb 是「大模型服务」（provides=model），不是「智能体」（agent）。
智能体是后续在 model 之上、用 mcp/skill 组建的更高层，不属于 registry 的能力清单。
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = PROJECT / "registry"


def _version_key(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(raw).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def _latest_version(root: Path) -> str:
    return sorted((p.name for p in root.iterdir() if p.is_dir()),
                  key=_version_key)[-1]


def _scan_catalog(plugins_dir: Path) -> dict[str, object]:
    """扫描 plugins/*/{version}/plugin.json，每 id 取最新版本生成 catalog。"""
    latest: dict[str, tuple[str, dict[str, object]]] = {}
    if plugins_dir.is_dir():
        for plugin_dir in plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            plugin_id = plugin_dir.name
            versions = [p for p in plugin_dir.iterdir() if p.is_dir()]
            if not versions:
                continue
            best = max(versions, key=lambda p: _version_key(p.name))
            manifest_path = best / "plugin.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(manifest, dict):
                continue
            latest[plugin_id] = (best.name, manifest)

    plugins = []
    for plugin_id in sorted(latest):
        version, manifest = latest[plugin_id]
        plugins.append({
            "id": str(manifest.get("id") or plugin_id),
            "name": str(manifest.get("name") or plugin_id),
            "description": str(manifest.get("description") or ""),
            "kind": str(manifest.get("kind") or ""),
            "latest_version": version,
            "requires_core": str(manifest.get("requires_core") or ""),
            "requires": list(manifest.get("requires") or []),
            "provides": manifest.get("provides"),
            "manifest_url": f"/plugins/{plugin_id}/{version}/plugin.json",
        })
    return {"schema": 1, "plugins": plugins}


class RegistryHandler(BaseHTTPRequestHandler):
    root: Path = DEFAULT_ROOT

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, raw: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str, status: int = 404) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json({"status": "ok", "service": "plateau-registry"})
            return

        if path == "/plugins/catalog.json":
            manual = self.root / "catalog.json"
            if manual.is_file():
                self._send_bytes(manual.read_bytes(), "application/json; charset=utf-8")
            else:
                self._send_json(_scan_catalog(self.root / "plugins"))
            return

        # /plugins/{id}/{version}/{...} 或 /runtimes/{id}/{version}/{...}
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 4 and parts[0] in ("plugins", "runtimes"):
            target = (self.root / Path(*parts)).resolve()
            section_root = (self.root / parts[0]).resolve()
            if target != section_root and section_root not in target.parents:
                self._send_text("forbidden", 403)
                return
            if not target.is_file():
                self._send_text("not found", 404)
                return
            content_type = "application/json; charset=utf-8" if target.suffix == ".json" \
                else "application/octet-stream"
            self._send_bytes(target.read_bytes(), content_type)
            return

        self._send_text("not found", 404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        print(f"[registry] {self.address_string()} {format % args}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="plateau-registry 开发态服务")
    _ = parser.add_argument("--port", type=int, default=8700)
    _ = parser.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()

    root = Path(args.root).resolve()
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    RegistryHandler.root = root

    server = ThreadingHTTPServer(("127.0.0.1", args.port), RegistryHandler)
    print(f"[registry] serving {root} on http://127.0.0.1:{args.port}", flush=True)
    print(f"[registry] catalog: http://127.0.0.1:{args.port}/plugins/catalog.json", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
