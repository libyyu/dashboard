# -*- coding: utf-8 -*-
"""临时测试：验证 _ensure_runtime 能下载/校验/解压 node 到共享运行时目录。"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

TEST_HOME = Path(tempfile.gettempdir()) / "plateau-test"
os.environ["PLATEAU_HOME"] = str(TEST_HOME)
os.environ["PLATEAU_PROFILE"] = "default"
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_HOME / 'test.db').as_posix()}"
os.environ["PLATEAU_REGISTRY_URL"] = "http://127.0.0.1:8700"

registry_proc = subprocess.Popen(
    [sys.executable, str(PROJECT / "scripts" / "registry_server.py"),
     "--root", str(PROJECT / "registry"), "--port", "8700"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(1.2)

failed = False
try:
    from app.core.storage import DatabaseConfig
    from app.adapters.sqlite_storage import SqliteStorage
    from app.routers.plugins import _ensure_runtime, _runtime_dir

    store = SqliteStorage(DatabaseConfig.from_env())
    print("[test] storage ok")

    print("[test] ensuring node@22.23.5 ...", flush=True)
    _ensure_runtime(store, "node@22.23.5")

    d = _runtime_dir("node", "22.23.5")
    node_bin = "node.exe" if os.name == "nt" else "node"
    node_path = d / node_bin
    complete = (d / ".complete").is_file()
    print("[test] runtime dir:", d)
    print("[test] .complete:", complete)
    print("[test] node_bin exists:", node_path.is_file())

    r = subprocess.run([str(node_path), "--version"], capture_output=True, text=True, timeout=30)
    print("[test] node --version:", r.stdout.strip(), "rc=", r.returncode)

    rows = store.query("runtimes", limit=10)
    print("[test] runtimes collection:", rows)

    failed = not (complete and node_path.is_file() and r.returncode == 0)
    print("[test] OK" if not failed else "[test] FAIL")
finally:
    registry_proc.terminate()
    try:
        registry_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        registry_proc.kill()

sys.exit(1 if failed else 0)
