"""四个版画/界面专属转场（PRD C4 / R-15）。

参考样本的术语：
- ``winclose``（收缩）：窗口像被合上一样向内收；
- ``zoom``（穿越）：推近穿过画面；
- ``shatter``（碎裂）：画面裂成若干块向外错开；
- ``slices``（切片）：横向条带左右错位。

全部是"给两个帧 + 进度 p ∈ [0,1] → 一帧"的纯函数，确定性、可单测；
场景脚本按拍点调用它们即可卡点转场。
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
from PIL import Image


def _to_array(image: Image.Image) -> np.ndarray:
    return np.array(image.convert("RGBA"), dtype=np.uint8)


def _to_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGBA")


def _ease(p: float) -> float:
    p = max(0.0, min(1.0, float(p)))
    return p * p * (3 - 2 * p)


def _resize(image: Image.Image, scale: float) -> Image.Image:
    width, height = image.size
    return image.resize(
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        Image.BILINEAR,
    )


def _place_center(canvas: Image.Image, layer: Image.Image) -> Image.Image:
    cw, ch = canvas.size
    lw, lh = layer.size
    canvas.alpha_composite(layer, ((cw - lw) // 2, (ch - lh) // 2))
    return canvas


def winclose(src: Image.Image, dst: Image.Image, p: float) -> Image.Image:
    """收缩：``src`` 向中心缩小并淡出，``dst`` 在后景淡入。"""
    e = _ease(p)
    canvas = dst.convert("RGBA").copy()
    scale = max(0.0, 1.0 - e)
    if scale > 0:
        shrunk = _resize(src.convert("RGBA"), scale)
        alpha = shrunk.getchannel("A").point(lambda v: int(v * (1.0 - e)))
        shrunk.putalpha(alpha)
        canvas = _place_center(canvas, shrunk)
    return canvas


def zoom(src: Image.Image, dst: Image.Image, p: float) -> Image.Image:
    """穿越：``src`` 放大并淡出（像镜头推近穿过去），露出 ``dst``。"""
    e = _ease(p)
    canvas = dst.convert("RGBA").copy()
    scale = 1.0 + 1.6 * e
    zoomed = _resize(src.convert("RGBA"), scale)
    alpha = zoomed.getchannel("A").point(lambda v: int(v * (1.0 - e)))
    zoomed.putalpha(alpha)
    return _place_center(canvas, zoomed)


def shatter(
    src: Image.Image,
    dst: Image.Image,
    p: float,
    *,
    cols: int = 6,
    rows: int = 4,
    spread_px: int = 60,
) -> Image.Image:
    """碎裂：把 ``src`` 切成 ``cols×rows`` 块，按进度向外错开并淡出。"""
    if cols <= 0 or rows <= 0:
        raise ValueError("cols/rows 必须为正")
    e = _ease(p)
    canvas = dst.convert("RGBA").copy()
    base = src.convert("RGBA")
    width, height = base.size
    tile_w = max(1, width // cols)
    tile_h = max(1, height // rows)
    for row in range(rows):
        for col in range(cols):
            box = (col * tile_w, row * tile_h,
                   min(width, (col + 1) * tile_w), min(height, (row + 1) * tile_h))
            tile = base.crop(box)
            alpha = tile.getchannel("A").point(lambda v: int(v * (1.0 - e)))
            tile.putalpha(alpha)
            cx, cy = col - (cols - 1) / 2.0, row - (rows - 1) / 2.0
            dx = int(round(cx * spread_px * e))
            dy = int(round(cy * spread_px * e))
            canvas.alpha_composite(tile, (box[0] + dx, box[1] + dy))
    return canvas


def slices(
    src: Image.Image,
    dst: Image.Image,
    p: float,
    *,
    bands: int = 8,
    max_offset_px: int = 40,
) -> Image.Image:
    """切片：横向条带按奇偶左右错位，逐条被 ``dst`` 替换。"""
    if bands <= 0:
        raise ValueError("bands 必须为正")
    e = _ease(p)
    canvas = dst.convert("RGBA").copy()
    base = _to_array(src)
    height = base.shape[0]
    band_h = max(1, height // bands)
    for index in range(bands):
        y0 = index * band_h
        y1 = min(height, y0 + band_h)
        if index / bands < e:
            continue  # 该条已被目标帧替换
        strip = base[y0:y1].copy()
        shift = int(round(max_offset_px * e * (1 if index % 2 == 0 else -1)))
        strip = np.roll(strip, shift, axis=1)
        canvas.alpha_composite(_to_image(strip), (0, y0))
    return canvas


#: 名称 → 函数（供类型/场景按名引用）。
TRANSITIONS = {
    "winclose": winclose,
    "zoom": zoom,
    "shatter": shatter,
    "slices": slices,
}


def apply(name: str, src: Image.Image, dst: Image.Image, p: float, **kwargs) -> Image.Image:
    """按名称应用转场。"""
    func = TRANSITIONS.get(name)
    if func is None:
        raise KeyError(f"未知转场 {name!r}；可用 {sorted(TRANSITIONS)}")
    return func(src, dst, p, **kwargs)


def decay_sequence(
    frames: Iterable[Image.Image],
    *,
    misregistration_px: int = 0,
    hold_modulus: int = 1,
) -> list[Image.Image]:
    """R-14 腐坏组合：按固定规律重复帧（降帧）+ 逐帧撕裂（可选）。

    ``hold_modulus=3`` 表示三帧取一帧（≈1/3 帧率）；``misregistration_px`` >0 时
    每帧叠加一次水平撕裂错位，形成"腐坏越走越坏"的推进感。
    """
    from clipwright.animation.plate.degrade import tear

    frames = list(frames)
    if hold_modulus <= 1 and misregistration_px <= 0:
        return frames
    out: list[Image.Image] = []
    for index, frame in enumerate(frames):
        if hold_modulus > 1 and index % hold_modulus != 0:
            out.append(out[-1] if out else frame)
        else:
            out.append(frame)
        if misregistration_px > 0:
            offset = int(misregistration_px * (index / max(1, len(frames) - 1)))
            if offset > 0:  # offset=0 等于没撕，不要画出断口红边
                out[-1] = tear(out[-1], offset_px=offset)
    return out


__all__ = [
    "TRANSITIONS",
    "apply",
    "decay_sequence",
    "shatter",
    "slices",
    "winclose",
    "zoom",
]
