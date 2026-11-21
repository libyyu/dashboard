# -*- coding: utf-8 -*-
"""用公司 Keystore（keystore-cli）给 Windows 可执行文件做 Authenticode 签名。

为什么必须签：
  - iOA 软件联网管控把「无发行商软件」默认禁止内/外网（实测回明文 403 code 190001，
    https 下表现为 TLS 错误 0x80090308）；签上 Tencent Technology (Shenzhen) Company
    Limited 证书即成为「公司指定软件」，默认放通。
  - SmartScreen / 电脑管家对未签名安装包报「危险」；EV 签名 + bleach（管家加白）消除提示。

用法：
  python scripts/keystore_sign.py <file> [<file> ...]
  （也被 tauri build 作为 bundle.windows.signCommand 调用，%1 = 待签文件）

环境变量：
  KEYSTORE_AUTH_ID      Keystore 应用授权 ID（keystore.woa.com → 应用 → 授权管理）
  KEYSTORE_AUTH_SECRET  对应密钥（创建 AuthID 时邮件下发）
  KEYSTORE_BIN          keystore-cli.exe 路径（1.3.3 起 Windows 包内文件名）
                        （默认 %USERPROFILE%\\.keystore\\keystore-cli.exe）
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_BIN = Path.home() / ".keystore" / "keystore-cli.exe"
ORGANIZATION = "Tencent Technology (Shenzhen) Company Limited"


def keystore_bin() -> Path:
    return Path(os.environ.get("KEYSTORE_BIN", "").strip() or DEFAULT_BIN)


def missing_prereqs() -> list[str]:
    """返回缺失的前置条件（空列表 = 可签名）。release.py 用它做发版前预检。"""
    missing = [name for name in ("KEYSTORE_AUTH_ID", "KEYSTORE_AUTH_SECRET") if not os.environ.get(name, "").strip()]
    if not keystore_bin().is_file():
        missing.append(f"KEYSTORE_BIN（{keystore_bin()} 不存在）")
    return missing


def signature_status(path: Path) -> str:
    """Authenticode 状态：Valid / NotSigned / HashMismatch / ..."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status.ToString()"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sign(files: list[Path]) -> None:
    missing = missing_prereqs()
    if missing:
        raise SystemExit(f"[sign] 缺少签名前置条件：{', '.join(missing)}")
    for f in files:
        if not f.is_file():
            raise SystemExit(f"[sign] 文件不存在：{f}")

    config = "\n".join([
        "account:",
        f"  authId: {_yaml_str(os.environ['KEYSTORE_AUTH_ID'].strip())}",
        f"  secret: {_yaml_str(os.environ['KEYSTORE_AUTH_SECRET'].strip())}",
        "signConfig:",
        "  windows:",
        "    type: digest",
        f"    organization: {_yaml_str(ORGANIZATION)}",
        "    signType: EVMode",
        f"    targetFiles: {_yaml_str(';'.join(str(f.resolve()) for f in files))}",
        "    outputDir: \"\"",  # 留空 = 原地替换
        "    bleach: true",     # 签名后追加电脑管家加白
        "    replace: true",    # 重复签名时替换旧签名，避免嵌套
        "",
    ])
    # 配置含密钥：写到私有临时文件，用完即删
    fd, cfg_path = tempfile.mkstemp(prefix="keystore-sign-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(config)
        proc = subprocess.run(
            [str(keystore_bin()), "windows", "sign", "act", "--config", cfg_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    finally:
        Path(cfg_path).unlink(missing_ok=True)
    out = proc.stdout.decode("utf-8", errors="replace")
    sys.stdout.write(out)
    if proc.returncode != 0:
        raise SystemExit(f"[sign] keystore-cli 失败（exit {proc.returncode}）")

    for f in files:
        status = signature_status(f)
        if status != "Valid":
            raise SystemExit(f"[sign] 签名后校验失败：{f} → {status}")
        print(f"[sign] OK {f.name}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        raise SystemExit("用法：python scripts/keystore_sign.py <file> [<file> ...]")
    sign([Path(a) for a in sys.argv[1:]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
