"""MotionSpec 校验：schema 之外的时间/属性/能力/质量检查。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clipwright.animation.motion_spec import MotionSpec

_ALLOWED = {
    "opacity", "translate_x", "translate_y", "translate_z", "scale", "scale_x", "scale_y",
    "rotate", "rotate_x", "rotate_y", "rotate_z", "skew_x", "skew_y", "perspective",
    "transform_origin", "font_size", "letter_spacing", "line_height", "color", "fill",
    "stroke_width", "stroke_color", "shadow", "blur", "clip_inset", "mask", "z_index",
}
_MAX_LAYERS = 8
_MAX_KFS = 16
_MAX_UNITS = 80


@dataclass
class MotionValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def validate_motion_spec(spec: MotionSpec, *, capabilities: set[str] | None = None,
                         caption_band_px: int = 160) -> MotionValidation:
    errors: list[str] = []
    warnings: list[str] = []
    caps = capabilities or set()
    dur = float(spec.duration_sec)
    if len(spec.layers) > _MAX_LAYERS:
        errors.append(f"layers {len(spec.layers)} > {_MAX_LAYERS}")
    for req in spec.requires:
        if capabilities and req not in caps:
            errors.append(f"unsupported capability: {req}")

    moving = 0
    last_move = 0.0
    for layer in spec.layers:
        if len(layer.keyframes) > _MAX_KFS:
            errors.append(f"{layer.id}: keyframes {len(layer.keyframes)} > {_MAX_KFS}")
        if len(layer.content) > _MAX_UNITS and layer.type == "text":
            errors.append(f"{layer.id}: text units > {_MAX_UNITS}")
        prev = -1.0
        for kf in layer.keyframes:
            if kf.time < 0 or kf.time > dur + 1e-6:
                errors.append(f"{layer.id}: keyframe time {kf.time} out of range")
            if kf.time < prev:
                errors.append(f"{layer.id}: keyframe times not ascending")
            prev = kf.time
            for key in (kf.model_extra or {}):
                if key not in _ALLOWED:
                    errors.append(f"{layer.id}: unsupported property {key}")
            vals = kf.model_extra or {}
            if any(k in vals for k in ("translate_x", "translate_y", "translate_z", "scale",
                                       "scale_x", "scale_y", "rotate", "rotate_x", "rotate_y",
                                       "rotate_z", "blur")):
                moving += 1
                last_move = max(last_move, kf.time)
        # 安全区粗检：布局必须落在 0..1 或安全像素内
        y = layer.layout.get("y", "center")
        if isinstance(y, (int, float)) and not (0 <= float(y) <= 1):
            warnings.append(f"{layer.id}: y 超出 0..1")
        if layer.type == "text" and isinstance(y, (int, float)) and float(y) > 0.88:
            warnings.append(f"{layer.id}: 文字接近字幕带 {caption_band_px}px")

    coverage = (last_move / dur) if dur > 0 else 0.0
    if moving == 0:
        errors.append("no observable motion")
    if coverage < 0.35:
        warnings.append(f"motion coverage low: {coverage:.2f}")
    return MotionValidation(ok=not errors, errors=errors, warnings=warnings,
                            metrics={"moving_keyframes": moving, "coverage": round(coverage, 3)})
