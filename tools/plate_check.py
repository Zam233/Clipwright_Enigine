"""版画库检查图（PRD 门 2 / R-06…R-10 的证据产物）。

生成一张 ``out/plate/check_sheet.png``：纸底 / 三墨套印 / 错版 / 撕裂 / 字符版画 / RGB 分离
六个格子，并输出 JSON 结论（含两次渲染是否逐字节一致）。

用法::

    python -m tools.plate_check
    python -m tools.plate_check --out out/plate/check_sheet.png --json

退出码：0 = 确定性通过；1 = 两次渲染不一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from clipwright.animation.plate import (
    DEFAULT_INK_PALETTE,
    compose,
    glyph_plate,
    layer_coverage,
    misregistration,
    rgb_split,
    split_inks,
    tear,
)
from clipwright.animation.plate.ground import render_ground

ROOT = Path(__file__).resolve().parents[1]

PANEL_W, PANEL_H = 640, 360


def draw_silhouette(size: tuple[int, int]) -> Image.Image:
    """画一个"无脸胸像"式的三色剪影（确定性，仅供检查用）。"""
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    me = (*DEFAULT_INK_PALETTE["me"], 255)
    you = (*DEFAULT_INK_PALETTE["you"], 255)
    ink = (*DEFAULT_INK_PALETTE["ink"], 255)

    # 肩/胸（橙）
    draw.ellipse((width * 0.18, height * 0.62, width * 0.82, height * 1.25), fill=me)
    # 头（米白）
    head_r = min(width, height) * 0.20
    cx, cy = width * 0.5, height * 0.46
    draw.ellipse((cx - head_r, cy - head_r, cx + head_r, cy + head_r), fill=you)
    # 头发/领结（近黑）
    draw.pieslice(
        (cx - head_r * 1.22, cy - head_r * 1.30, cx + head_r * 1.22, cy + head_r * 0.72),
        start=185,
        end=355,
        fill=ink,
    )
    draw.polygon(
        [
            (cx - head_r * 0.30, cy + head_r * 0.86),
            (cx + head_r * 0.30, cy + head_r * 0.86),
            (cx, cy + head_r * 1.20),
        ],
        fill=ink,
    )
    return canvas


def _caption(panel: Image.Image, text: str) -> Image.Image:
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, panel.height - 26, panel.width, panel.height), fill=(0, 0, 0))
    draw.text((10, panel.height - 21), text, fill=(230, 226, 218))
    return panel


def build_sheet() -> Image.Image:
    ground = render_ground(PANEL_W, PANEL_H, seed=20261018)
    silhouette = draw_silhouette((PANEL_W, PANEL_H))
    layers = split_inks(silhouette)

    normal = ground.convert("RGBA").copy()
    normal.alpha_composite(compose(layers, size=(PANEL_W, PANEL_H)))

    misreg = ground.convert("RGBA").copy()
    misreg.alpha_composite(
        compose(misregistration(layers, offset_px=28), size=(PANEL_W, PANEL_H))
    )

    torn = tear(normal, offset_px=30)

    plate = glyph_plate(normal, cols=48, cell=13)

    split = rgb_split(normal, dx=4)

    panels = [
        (ground, "1 ground() 纸底"),
        (normal, "2 inks()+compose() 三墨套印"),
        (misreg, "3 misregistration(28px) 错版"),
        (torn, "4 tear(30px) 撕裂"),
        (plate, "5 glyph_plate() 字符版画"),
        (split, "6 rgbSplit(4px) RGB 分离"),
    ]
    sheet = Image.new("RGB", (PANEL_W * 3, PANEL_H * 2), (8, 8, 10))
    for index, (panel, caption) in enumerate(panels):
        prepared = panel.convert("RGB").resize((PANEL_W, PANEL_H))
        sheet.paste(_caption(prepared, caption), ((index % 3) * PANEL_W, (index // 3) * PANEL_H))
    return sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="版画库检查图（门 2 证据）")
    parser.add_argument("--out", default=str(ROOT / "out" / "plate" / "check_sheet.png"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    first = build_sheet()
    second = build_sheet()
    first_bytes = _png_bytes(first)
    second_bytes = _png_bytes(second)
    digest = hashlib.sha256(first_bytes).hexdigest()

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(first_bytes)

    silhouette = draw_silhouette((PANEL_W, PANEL_H))
    coverage = layer_coverage(split_inks(silhouette))
    payload: dict[str, Any] = {
        "ok": first_bytes == second_bytes,
        "path": str(target),
        "sha256": digest,
        "deterministic": first_bytes == second_bytes,
        "panel_size": [PANEL_W, PANEL_H],
        "ink_coverage": coverage,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"检查图: {target}")
        print(f"三个墨版像素数: {coverage}")
        print(f"两次渲染一致: {payload['deterministic']}  sha256={digest[:16]}…")
    return 0 if payload["deterministic"] else 1


def _png_bytes(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
