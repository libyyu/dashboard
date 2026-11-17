# -*- coding: utf-8 -*-
"""冻结 desktop/launcher.py -> launcher.exe（onedir）。

runtime 三个 stdlib 模块由 launcher 按文件路径加载（spec_from_file_location），
必须显式打进 _internal/runtime/。冻结版 services.json 由 build.py 放到
launcher.exe 同级（不进 _internal，因为 launcher._base_dir() 冻结态 = exe 所在目录）。
"""
from pathlib import Path

_ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(_ROOT / "desktop" / "launcher.py")],
    pathex=[str(_ROOT / "desktop")],
    # _pid_alive 在 Windows 分支动态 import ctypes；显式声明避免冻结后缺 _ctypes
    hiddenimports=["ctypes", "_ctypes"],
    datas=[
        (str(_ROOT / "backend" / "app" / "runtime" / "ports.py"), "runtime"),
        (str(_ROOT / "backend" / "app" / "runtime" / "log_policy.py"), "runtime"),
        (str(_ROOT / "backend" / "app" / "runtime" / "logging_setup.py"), "runtime"),
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="launcher",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="launcher",
)
