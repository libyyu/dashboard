# -*- coding: utf-8 -*-
"""稳定端口分配与 runtime state 持久化。

固定端口在「安装版 / 开发版 / 多 profile」并存时会互相争用，甚至误连上一个
没死透的进程。这里按 profile id 用 FNV-1a 派生端口并落盘复用；占用时只在派生
端口附近小范围回退，禁止全局扫描（否则会连到别人的服务）。

借鉴 EvoFlow 的 gateway_port_for_profile / resolve_backend_port_flexible 思路。
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

BASE_PORT = 8100
PORT_RANGE = 900  # 8100..8999，避开 7077(cb)、8899(开发默认)、9099(不在本包范围)
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK64 = 0xFFFFFFFFFFFFFFFF


def stable_port(key: str) -> int:
    """FNV-1a 64 位哈希 → 8100..8999 的稳定端口。"""
    h = _FNV_OFFSET
    for byte in key.encode("utf-8"):
        h = ((h ^ byte) * _FNV_PRIME) & _MASK64
    return BASE_PORT + h % PORT_RANGE


def profile_id() -> str:
    return os.environ.get("PLATEAU_PROFILE", "default")


def home_dir() -> Path:
    raw = os.environ.get("PLATEAU_HOME")
    return Path(raw) if raw else Path.home() / ".plateau-dashboard"


def state_path() -> Path:
    return home_dir() / "profiles" / profile_id() / "runtime_state.json"


def read_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def resolve_port(service: str) -> int:
    """优先级：PLATEAU_<SERVICE>_PORT > runtime state > 哈希端口；占用时 +1..+8 回退。

    :param service: 服务名（dashboard / cb）
    :return: 可用端口
    :raises RuntimeError: 小范围回退内仍无空闲端口
    """
    env_key = "PLATEAU_" + service.upper().replace("-", "_") + "_PORT"
    state = read_state()
    ports = state.get("ports")
    if not isinstance(ports, dict):
        ports = {}
    raw = os.environ.get(env_key) or ports.get(service)
    base = int(raw) if raw else stable_port(f"{profile_id()}:{service}")
    for offset in range(9):
        candidate = base + offset
        if 1 <= candidate <= 65535 and port_is_free(candidate):
            if candidate != base:
                print(f"[WARN] {service}: 端口 {base} 被占用，回退到 {candidate}")
            ports[service] = candidate
            state["ports"] = ports
            write_state(state)
            return candidate
    raise RuntimeError(f"{service}: {base}..{base + 8} 范围内无可用端口")
