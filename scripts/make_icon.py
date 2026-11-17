# -*- coding: utf-8 -*-
"""生成 Plateau 桌面图标（多尺寸 ico）。

设计：深蓝渐变圆角底 + 三层「台地 / plateau」+ 顶部节点。
纯 Pillow 绘制，无外部素材依赖；如需替换为正式品牌图，直接覆盖
desktop/src-tauri/icons/icon.ico 即可（tauri.conf.json 的 bundle.icon
与 windows.nsis.installerIcon 都指向该文件）。

用法：python scripts/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "desktop" / "src-tauri" / "icons" / "icon.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

S = 1024  # 超采样后缩小，保证小尺寸图标边缘平滑


def _gradient() -> Image.Image:
    grad = Image.new("RGB", (S, S))
    draw = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        draw.line(
            [(0, y), (S, y)],
            fill=(int(11 + 27 * t), int(18 + 68 * t), int(32 + 168 * t)),
        )
    return grad


def build() -> Image.Image:
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=216, fill=255)
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_gradient(), (0, 0), mask)
    draw = ImageDraw.Draw(canvas)

    draw.ellipse([472, 176, 552, 256], fill=(126, 176, 255, 255))
    # 三层台地：上窄下宽，透明度逐层降低形成纵深
    layers = (
        ([318, 352, 706, 416], 255),
        ([236, 494, 788, 558], 220),
        ([154, 636, 870, 700], 185),
    )
    for box, alpha in layers:
        draw.rounded_rectangle(box, radius=30, fill=(230, 240, 255, alpha))
    return canvas


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    build().save(TARGET, format="ICO", sizes=SIZES)
    print(f"[OK] {TARGET} ({TARGET.stat().st_size} bytes, sizes={[s[0] for s in SIZES]})")


if __name__ == "__main__":
    main()
