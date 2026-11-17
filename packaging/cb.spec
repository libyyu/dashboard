# -*- coding: utf-8 -*-
"""冻结 cb/server.py -> cb.exe（onedir）。

cb 仓库路径由环境变量 PLATEAU_CB_REPO 覆盖，默认取开发机 RuoYi cb。
codebuddy-headless.exe 不进 exe，由 build.py 放到包内 cb/bin/ 并靠
CODEBUDDY_CODE_PATH 环境变量指向。
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

_ROOT = Path(SPECPATH).resolve().parent
_CB = Path(
    os.environ.get("PLATEAU_CB_REPO")
    or str(_ROOT.parent.parent / "RuoYi-Vue3-FastAPI" / "cb")
)

a = Analysis(
    [str(_CB / "server.py")],
    pathex=[str(_CB)],
    datas=[],
    hiddenimports=collect_submodules("cbopenai") + collect_submodules("codebuddy_agent_sdk"),
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cb",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="cb",
)
