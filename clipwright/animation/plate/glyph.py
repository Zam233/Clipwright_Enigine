"""字符版画（PRD C3 R-09）。

参考样本的要求：每格超采样、按墨色用不同字符梯度、边缘用有方向的字符、
裁切在轮廓内、编码后仍看得清。实现取"够用且确定"的路线：

- 把图缩到 ``cols × rows`` 的格网，每格取平均亮度；
- 亮度 → 字符梯度索引（浅色用密字符、深色用疏字符，可配 ``invert``）；
- 每个字符按该格**最近的墨色**上色（三墨分版后的颜色语义）；
- 轮廓格（与背景交界）用 ``edge_chars`` 强调，模拟"裁切在轮廓内"的观感；
- 用等宽字体逐字绘制，字号按格子高度算，输出尺寸可指定 ``cell``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from clipwright.animation.plate.inks import DEFAULT_INK_PALETTE

DEFAULT_CHARS = " .:-=+*#%@"
EDGE_CHARS = "|/\\-"


def _load_font(size: int) -> ImageFont.ImageFont:
    """优先等宽系统字体（可缩放），否则退回 Pillow 默认字体。

    实测：Pillow 的 ``load_default`` 在老版本不接受 ``size``，会得到固定小字形，
    整张字符画会退化成"一串小点"——这是门 2 自查里最容易翻车的地方。
    """
    candidates = (
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:  # pragma: no cover - 字体损坏
                continue
    try:  # Pillow ≥ 10
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - 老版本 Pillow
        return ImageFont.load_default()


def nearest_ink_name(
    pixel: tuple[int, int, int],
    palette: Mapping[str, tuple[int, int, int]] | None = None,
) -> str:
    palette = dict(palette or DEFAULT_INK_PALETTE)
    weights = np.array([0.30, 0.59, 0.11])
    best, best_dist = "ink", float("inf")
    for name, color in palette.items():
        dist = float(((np.array(pixel) - np.array(color)) ** 2 * weights).sum())
        if dist < best_dist:
            best, best_dist = name, dist
    return best


def glyph_plate(
    image: Image.Image,
    *,
    cols: int = 96,
    cell: int = 12,
    chars: str = DEFAULT_CHARS,
    edge_chars: str = EDGE_CHARS,
    palette: Mapping[str, tuple[int, int, int]] | None = None,
    background: tuple[int, int, int] = (18, 16, 14),
    invert: bool = False,
    alpha_threshold: int = 16,
    supersample: int = 2,
    gamma: float = 0.72,
) -> Image.Image:
    """把图片渲成字符版画（RGB）。

    ``supersample``：先按 2 倍尺寸绘制再缩小——小字号下 char 笔画太细会退化成
    "一串小点"（门 2 自查实拍踩到），超采样后才有"字符版画"的密度。
    ``gamma`` <1 会把中间调往密字符推（橙色这类中亮度色块不会只剩一个点）。
    """
    if cols <= 1 or cell <= 0:
        raise ValueError("cols 必须 >1 且 cell 必须为正")
    ss = max(1, int(supersample))
    rgba = image.convert("RGBA")
    width, height = rgba.size
    rows = max(1, int(round(cols * height / max(width, 1))))

    small = rgba.resize((cols, rows), Image.LANCZOS)
    arr = np.array(small, dtype=np.float64)
    alpha = arr[..., 3]
    luma = (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114) / 255.0
    opaque = alpha > alpha_threshold
    if invert:
        luma = 1.0 - luma
    if gamma > 0 and gamma != 1.0:
        luma = np.power(np.clip(luma, 0.0, 1.0), float(gamma))

    big_cell = cell * ss
    out = Image.new("RGB", (cols * big_cell, rows * big_cell), background)
    draw = ImageDraw.Draw(out)
    font = _load_font(max(6, int(big_cell * 1.02)))
    palette_map = dict(palette or DEFAULT_INK_PALETTE)

    for row in range(rows):
        for col in range(cols):
            if not opaque[row, col]:
                continue
            value = float(np.clip(luma[row, col], 0.0, 1.0))
            char_index = int(round(value * (len(chars) - 1)))
            char = chars[char_index]
            if char == " ":
                char = "."
            if _is_edge(opaque, row, col):
                char = edge_chars[(row + col) % len(edge_chars)]
            ink = nearest_ink_name(
                (int(arr[row, col, 0]), int(arr[row, col, 1]), int(arr[row, col, 2])),
                palette_map,
            )
            draw.text((col * big_cell, row * big_cell), char, fill=palette_map[ink], font=font)
    if ss > 1:
        out = out.resize((cols * cell, rows * cell), Image.LANCZOS)
    return out


def _is_edge(opaque: np.ndarray, row: int, col: int) -> bool:
    rows, cols = opaque.shape
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        yy, xx = row + dy, col + dx
        if yy < 0 or xx < 0 or yy >= rows or xx >= cols:
            return True
        if not opaque[yy, xx]:
            return True
    return False


def gradient_palette(base: str, *, steps: int = 3) -> list[str]:
    """给某墨色生成"亮/中/暗"三档字符梯度（供逐墨定制）。"""
    if steps <= 0:
        raise ValueError("steps 必须为正")
    usable = [c for c in DEFAULT_CHARS if c != " "]
    if steps >= len(usable):
        return usable
    stride = max(1, len(usable) // steps)
    return usable[::stride][:steps] or usable[:steps]


def sequence(chars: str) -> Sequence[str]:
    return tuple(chars)


__all__ = ["DEFAULT_CHARS", "EDGE_CHARS", "gradient_palette", "glyph_plate", "nearest_ink_name"]
