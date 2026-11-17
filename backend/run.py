# -*- coding: utf-8 -*-
"""开发/生产启动入口：python run.py（在 backend/ 目录下运行）。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402
from app.runtime import log_policy  # noqa: E402
from app.runtime.logging_setup import configure_logging  # noqa: E402
from app.runtime.ports import resolve_port  # noqa: E402

if __name__ == "__main__":
    # 日志装配必须早于 uvicorn：log_config=None 防止 uvicorn 的 dictConfig 重置 root handler
    log_root = log_policy.logs_dir()
    configure_logging(
        "dashboard", log_root, log_policy.daily_log_path,
        console=True, max_file_bytes=log_policy.max_bytes(),
    )
    _ = log_policy.prune_daily_logs(log_root)

    # 显式 PORT 优先（兼容旧启动方式），否则按 profile 派生稳定端口并持久化
    explicit = os.environ.get("PORT")
    port = int(explicit) if explicit else resolve_port("dashboard")
    uvicorn.run(
        app, host="127.0.0.1", port=port, log_level="info",
        log_config=None, access_log=False,
    )
