"""版画/印刷能力库（PRD Part II C3 / M1）。

九个原语，分四个模块：

- ``inks``：三墨分版、alpha 清理、套印（``split_inks`` / ``clean_alpha`` / ``compose``）；
- ``ground``：版画纸底（噪点、点阵、暗角、四角裁切标记）；
- ``degrade``：腐坏三件套——错版、撕裂、RGB 分离、降帧取样；
- ``glyph``：字符版画（按墨色梯度、轮廓强调）；
- ``vectorize``：掩膜 → 多边形/SVG path（4K 特写用）；
- ``registration``：剪影配准表与按比例派生尺寸。

全部纯函数、确定性（无时钟、无全局随机），供场景脚本、CLI 检查与单测共用。
"""

from __future__ import annotations

from clipwright.animation.plate.degrade import (
    MISREGISTRATION_STEPS,
    decimate_indices,
    hold_frames_ok,
    misregistration,
    rgb_split,
    tear,
)
from clipwright.animation.plate.glyph import (
    DEFAULT_CHARS,
    EDGE_CHARS,
    glyph_plate,
    nearest_ink_name,
)
from clipwright.animation.plate.ground import render_ground
from clipwright.animation.plate.inks import (
    DEFAULT_INK_PALETTE,
    INK_ORDER,
    clean_alpha,
    compose,
    layer_coverage,
    split_inks,
)
from clipwright.animation.plate.registration import (
    Registration,
    apply_registration,
    derive_size,
    load_registration,
    registration_error,
    save_registration,
)
from clipwright.animation.plate.vectorize import (
    boundary_pixels,
    mask_to_svg_path,
    polygon_area,
    simplify,
    trace_contours,
)

__all__ = [
    "DEFAULT_CHARS",
    "DEFAULT_INK_PALETTE",
    "EDGE_CHARS",
    "INK_ORDER",
    "MISREGISTRATION_STEPS",
    "Registration",
    "apply_registration",
    "boundary_pixels",
    "clean_alpha",
    "compose",
    "decimate_indices",
    "derive_size",
    "glyph_plate",
    "hold_frames_ok",
    "layer_coverage",
    "load_registration",
    "mask_to_svg_path",
    "misregistration",
    "nearest_ink_name",
    "polygon_area",
    "registration_error",
    "render_ground",
    "rgb_split",
    "save_registration",
    "simplify",
    "split_inks",
    "tear",
    "trace_contours",
]
