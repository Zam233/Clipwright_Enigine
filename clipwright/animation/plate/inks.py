"""三墨分版（PRD C3 R-06）。

版画质感来自"一张图按三种墨色拆成三张蒙版，再各自着色、套印"。本模块只做
**纯函数式的图层拆合**，不做时间、不做 IO：

- ``clean_alpha``：alpha < 阈值清零（参考样本踩过：不清零会在描边/发光时出脏点）；
- ``split_inks``：按最接近的墨色把不透明像素分配给三个墨版；
- ``compose``：按可配置位移套印（错版由此而来，见 ``degrade.misregistration``）。

颜色语义（我=橙、你=米白、错误=红、缺席=灰）由 Persona 提供，本模块只接受调色板。
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
from PIL import Image

#: 三墨默认调色板（橙＝我、米白＝你、近黑＝墨）。可在类型/persona 层覆盖。
DEFAULT_INK_PALETTE: dict[str, tuple[int, int, int]] = {
    "me": (255, 138, 61),
    "you": (242, 234, 216),
    "ink": (14, 14, 18),
}

#: 人类可读的墨版名（渲染/报告里用）。
INK_ORDER: tuple[str, ...] = ("me", "you", "ink")


def clean_alpha(image: Image.Image, threshold: int = 16) -> Image.Image:
    """把 alpha 低于阈值的像素清零，避免描边/发光时出现脏点。"""
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[..., 3]
    rgba[..., 3] = np.where(alpha < int(threshold), 0, alpha)
    return Image.fromarray(rgba, mode="RGBA")


def _ink_matrix(palette: Mapping[str, tuple[int, int, int]], names: tuple[str, ...]) -> np.ndarray:
    return np.array([palette[name] for name in names], dtype=np.float64)


def split_inks(
    image: Image.Image,
    palette: Mapping[str, tuple[int, int, int]] | None = None,
    *,
    names: tuple[str, ...] = INK_ORDER,
    threshold: int = 16,
    tint: bool = True,
) -> dict[str, Image.Image]:
    """把图片拆成若干个墨版（每个墨版都是完整画布的 RGBA）。

    ``tint=True`` 时每个墨版被染成对应墨色；``False`` 时保留原像素颜色
    （用于"分版后再统一改色"的场景）。
    """
    palette = dict(palette or DEFAULT_INK_PALETTE)
    for name in names:
        if name not in palette:
            raise KeyError(f"调色板缺少墨色 {name!r}")

    rgba = np.array(clean_alpha(image, threshold).convert("RGBA"), dtype=np.uint8)
    opaque = rgba[..., 3] > 0
    if not opaque.any():
        out_shape = (rgba.shape[0], rgba.shape[1], 4)
        return {name: Image.new("RGBA", (rgba.shape[1], rgba.shape[0]), (0, 0, 0, 0)) for name in names}

    rgb = rgba[..., :3].astype(np.float64)
    ink = _ink_matrix(palette, names)
    # 距离：按 (r,g,b) 的加权欧氏距离（绿通道权重略高，贴近人眼）
    weights = np.array([0.30, 0.59, 0.11])
    dists = np.stack(
        [((rgb - color) ** 2 * weights).sum(axis=-1) for color in ink], axis=0
    )
    owner = dists.argmin(axis=0)

    layers: dict[str, Image.Image] = {}
    for index, name in enumerate(names):
        layer = np.zeros_like(rgba)
        selected = opaque & (owner == index)
        if tint:
            layer[selected, 0] = palette[name][0]
            layer[selected, 1] = palette[name][1]
            layer[selected, 2] = palette[name][2]
        else:
            layer[selected, :3] = rgba[selected, :3]
        layer[selected, 3] = rgba[selected, 3]
        layers[name] = Image.fromarray(layer, mode="RGBA")
    return layers


def layer_coverage(layers: Mapping[str, Image.Image]) -> dict[str, int]:
    """每个墨版的不透明像素数（用于检查"分配是否覆盖全部不透明像素"）。"""
    return {
        name: int((np.array(image.convert("RGBA"))[..., 3] > 0).sum())
        for name, image in layers.items()
    }


def compose(
    layers: Mapping[str, Image.Image],
    *,
    offsets: Mapping[str, tuple[int, int]] | None = None,
    order: tuple[str, ...] = INK_ORDER,
    size: tuple[int, int] | None = None,
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> Image.Image:
    """按顺序套印墨版；``offsets`` 给每个墨版加位移（错版）。"""
    offsets = offsets or {}
    if size is None:
        first = next(iter(layers.values()), None)
        size = first.size if first is not None else (1920, 1080)
    canvas = Image.new("RGBA", size, background)
    for name in order:
        layer = layers.get(name)
        if layer is None:
            continue
        offset = offsets.get(name, (0, 0))
        if offset != (0, 0):
            shifted = Image.new("RGBA", size, (0, 0, 0, 0))
            shifted.paste(layer, (int(offset[0]), int(offset[1])))
            layer = shifted
        canvas.alpha_composite(layer)
    return canvas


__all__ = [
    "DEFAULT_INK_PALETTE",
    "INK_ORDER",
    "clean_alpha",
    "compose",
    "layer_coverage",
    "split_inks",
]
