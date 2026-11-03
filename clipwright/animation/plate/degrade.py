"""腐坏三件套（PRD C3 R-08/R-09）：错版、撕裂、RGB 分离、降帧。

参考样本的定义：
- **错版**：三个墨版故意套不准（10/18/28px），边缘露出一条细红版；
- **撕裂**：图案水平或竖直错开两半，图案至少保持 6 帧（帧数由场景脚本控制，这里给参数）；
- **降帧**：按固定帧率取样（只依赖 t，不是真的丢帧编码）；
- **RGB 分离**：红蓝通道错位（**人物身上禁用**——有脸的画面只用错版与降帧）。

所有函数都是纯函数 + 确定性，不读时钟、不用全局随机。
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
from PIL import Image

#: 参考样本里的三档错版位移（px）。
MISREGISTRATION_STEPS: tuple[int, ...] = (10, 18, 28)


#: 错版时露出的细红版（错误红＝#f5506f）。
ERR_RED: tuple[int, int, int, int] = (245, 80, 111, 255)


def _outline(layer: Image.Image, px: int, color: tuple[int, int, int, int]) -> Image.Image:
    """给图层 alpha 边界描一圈细边（错版时露出的"红版"）。"""
    if px <= 0:
        return layer
    rgba = np.array(layer.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[..., 3] > 0
    if not alpha.any():
        return layer
    edge = np.zeros_like(alpha)
    for dy in range(-px, px + 1):
        for dx in range(-px, px + 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.zeros_like(alpha)
            h, w = alpha.shape
            ys = slice(max(0, dy), h + min(0, dy))
            xs = slice(max(0, dx), w + min(0, dx))
            ys_src = slice(max(0, -dy), h + min(0, -dy))
            xs_src = slice(max(0, -dx), w + min(0, -dx))
            shifted[ys, xs] = alpha[ys_src, xs_src]
            edge |= alpha & ~shifted
    out = rgba.copy()
    out[edge] = color
    return Image.fromarray(out, mode="RGBA")


def misregistration(
    layers: Mapping[str, Image.Image],
    *,
    offset_px: int = 10,
    direction: tuple[int, int] = (1, 1),
    order: tuple[str, ...] = ("me", "you", "ink"),
    red_plate_px: int = 2,
    red_color: tuple[int, int, int, int] = ERR_RED,
) -> dict[str, Image.Image]:
    """按档位把各墨版错开，并让边缘露出一条细红版；返回新图层字典。

    ``red_plate_px=0`` 关闭红版（例如有脸的画面，参考样本要求"有脸只用错版与降帧，
    不加撕裂"，红版仍可用但需人审）。
    """
    offset_px = int(offset_px)
    result: dict[str, Image.Image] = {}
    for index, name in enumerate(order):
        layer = layers.get(name)
        if layer is None:
            continue
        dx = int(round(offset_px * (index + 1) * direction[0]))
        dy = int(round(offset_px * (index + 1) * direction[1]))
        if red_plate_px > 0:
            layer = _outline(layer, red_plate_px, red_color)
        if dx == 0 and dy == 0:
            result[name] = layer
            continue
        canvas = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        canvas.paste(layer, (dx, dy))
        result[name] = canvas
    return result


def rgb_split(
    image: Image.Image,
    *,
    dx: int = 3,
    dy: int = 0,
    channels: tuple[str, str] = ("r", "b"),
) -> Image.Image:
    """RGB 分离：把两个通道各错开 ``dx/dy`` 像素。"""
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    out = rgba.copy()

    def shift(arr: np.ndarray, channel: int, sx: int, sy: int) -> None:
        if sx == 0 and sy == 0:
            return
        src = np.zeros_like(arr[..., channel])
        src[max(0, sy) : arr.shape[0] + min(0, sy), max(0, sx) : arr.shape[1] + min(0, sx)] = arr[
            max(0, -sy) : arr.shape[0] - max(0, sy) or None,
            max(0, -sx) : arr.shape[1] - max(0, sx) or None,
            channel,
        ]
        arr[..., channel] = src

    shift(out, 0 if channels[0] == "r" else 1, int(dx), int(dy))
    shift(out, 2 if channels[1] == "b" else 1, -int(dx), -int(dy))
    return Image.fromarray(out, mode="RGBA")


def tear(
    image: Image.Image,
    *,
    direction: str = "horizontal",
    band: tuple[float, float] = (0.35, 0.65),
    offset_px: int = 28,
    mask_color: tuple[int, int, int, int] | None = (245, 80, 111, 255),
    edge_px: int = 2,
) -> Image.Image:
    """撕裂：在 ``band`` 区间内把画面整体错开，并在断口画一条细边（可选）。"""
    if direction not in {"horizontal", "vertical"}:
        raise ValueError("direction 只能是 horizontal / vertical")
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    height, width = rgba.shape[:2]
    out = rgba.copy()
    lo = int(max(0.0, min(1.0, band[0])) * (height if direction == "horizontal" else width))
    hi = int(max(0.0, min(1.0, band[1])) * (height if direction == "horizontal" else width))
    lo, hi = min(lo, hi), max(lo, hi)

    if direction == "horizontal":
        out[lo:hi] = np.roll(rgba[lo:hi], int(offset_px), axis=1)
        if mask_color and edge_px > 0:
            out[lo : min(lo + edge_px, height), :, :] = mask_color
            out[max(hi - edge_px, 0) : hi, :, :] = mask_color
    else:
        out[:, lo:hi] = np.roll(rgba[:, lo:hi], int(offset_px), axis=0)
        if mask_color and edge_px > 0:
            out[:, lo : min(lo + edge_px, width), :] = mask_color
            out[:, max(hi - edge_px, 0) : hi, :] = mask_color
    return Image.fromarray(out, mode="RGBA")


def decimate_indices(
    frame_count: int,
    *,
    source_fps: float,
    target_fps: float,
) -> list[int]:
    """降帧：返回每一输出帧应取用的源帧序号（纯由帧率决定，只依赖 t）。"""
    if source_fps <= 0 or target_fps <= 0:
        raise ValueError("帧率必须为正")
    if frame_count <= 0:
        return []
    if target_fps >= source_fps:
        return list(range(frame_count))
    step = source_fps / target_fps
    indices: list[int] = []
    for index in range(frame_count):
        sample = int(index // step) * int(round(step))
        indices.append(min(frame_count - 1, sample))
    return indices


def hold_frames_ok(pattern_frame_count: int, *, min_hold: int = 6) -> bool:
    """图案保持帧数检查（撕裂/花屏图案至少保持 6 帧）。"""
    return int(pattern_frame_count) >= int(min_hold)


__all__ = [
    "MISREGISTRATION_STEPS",
    "decimate_indices",
    "hold_frames_ok",
    "misregistration",
    "rgb_split",
    "tear",
]
