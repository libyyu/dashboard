# -*- coding: utf-8 -*-
"""冻结 backend/run.py -> dashboard.exe（onedir）。

frontend/dist 与 VERSION 作为 data 打进 _internal，backend 的 resource_dir()
冻结态读 sys._MEIPASS 定位。
"""
from pathlib import Path

_ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(_ROOT / "backend" / "run.py")],
    pathex=[str(_ROOT / "backend")],
    datas=[
        (str(_ROOT / "frontend" / "dist"), "frontend/dist"),
        (str(_ROOT / "VERSION"), "."),
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dashboard",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="dashboard",
)
