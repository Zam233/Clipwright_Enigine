"""版画纸底（PRD C3 R-07）：暖色近黑 + 噪点 + 点阵 + 四角裁切标记。

必须是**确定性**的：同一 ``seed`` 两次渲染逐字节相同，否则逐帧哈希闸会失败。
因此这里用 ``numpy.random.RandomState(seed)``，绝不用全局随机。
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def render_ground(
    width: int = 1920,
    height: int = 1080,
    *,
    seed: int = 20261018,
    base: tuple[int, int, int] = (22, 18, 16),
    noise: float = 0.050,
    dots: float = 0.0022,
    dot_color: tuple[int, int, int] = (58, 48, 40),
    vignette: float = 0.42,
    corner_marks: bool = True,
    dot_radius: int = 1,
) -> Image.Image:
    """渲染一张版画纸底（RGB）。"""
    if width <= 0 or height <= 0:
        raise ValueError("width/height 必须为正")
    rng = np.random.RandomState(int(seed) & 0xFFFFFFFF)

    base_arr = np.zeros((height, width, 3), dtype=np.float64)
    base_arr[..., 0] = base[0]
    base_arr[..., 1] = base[1]
    base_arr[..., 2] = base[2]

    if noise > 0:
        grain = rng.normal(0.0, noise * 255.0, size=(height, width, 1))
        base_arr = np.clip(base_arr + grain, 0, 255)

    if vignette > 0:
        yy, xx = np.mgrid[0:height, 0:width]
        cy, cx = (height - 1) / 2.0, (width - 1) / 2.0
        radius = np.sqrt(((yy - cy) / max(cy, 1e-6)) ** 2 + ((xx - cx) / max(cx, 1e-6)) ** 2)
        mask = np.clip(1.0 - vignette * (radius / 1.42) ** 2, 0.0, 1.0)
        base_arr *= mask[..., None]

    image = Image.fromarray(np.clip(base_arr, 0, 255).astype(np.uint8), mode="RGB")

    if dots > 0:
        count = int(dots * width * height)
        xs = rng.randint(0, width, size=max(count, 0))
        ys = rng.randint(0, height, size=max(count, 0))
        draw = ImageDraw.Draw(image)
        for x, y in zip(xs.tolist(), ys.tolist()):
            draw.ellipse(
                (x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius),
                fill=dot_color,
            )

    if corner_marks:
        draw = ImageDraw.Draw(image)
        margin = max(8, int(min(width, height) * 0.02))
        length = max(16, int(min(width, height) * 0.05))
        color = (86, 74, 62)
        for ax, ay, dx, dy in (
            (margin, margin, 1, 1),
            (width - margin, margin, -1, 1),
            (margin, height - margin, 1, -1),
            (width - margin, height - margin, -1, -1),
        ):
            draw.line((ax, ay, ax + dx * length, ay), fill=color, width=2)
            draw.line((ax, ay, ax, ay + dy * length), fill=color, width=2)

    return image


__all__ = ["render_ground"]
