"""MG JSON Schema 验证器 — 校验和修复 LLM 生成的 MG JSON。"""

from __future__ import annotations

from typing import Any

REQUIRED_TOP_KEYS = {"animation_id", "elements"}
VALID_ELEMENT_TYPES = {"text", "shape", "line", "circle", "ring", "arc", "bg", "image"}
VALID_SHAPES = {"rect", "ellipse"}
VALID_POSITIONS = {"center", "left", "right", "top", "bottom"}
ANIMATABLE_PROPS = {
    "opacity", "scale", "translate_x", "translate_y", "rotate",
    "width", "height", "font_size", "color", "background",
    "border_radius", "border_width", "border_color",
    "box_shadow", "text_shadow", "filter", "letter_spacing",
    "font_weight", "font_family", "line_height", "transform_origin",
    "easing",
}
# easing 取值：命名曲线 + 可选 cubic-bezier 数组 [x1,y1,x2,y2]
VALID_EASINGS = {
    "linear", "ease", "ease-in", "ease-out", "ease-in-out",
    "back-out", "elastic-out", "bounce",
}


def validate_mg_json(mg_def: dict[str, Any]) -> tuple[bool, list[str]]:
    """验证 MG JSON 定义是否符合规范。"""
    errors: list[str] = []

    if not isinstance(mg_def, dict):
        return False, ["mg_def is not a dict"]

    for key in REQUIRED_TOP_KEYS:
        if key not in mg_def:
            errors.append(f"Missing required top-level key: {key}")

    anim_id = mg_def.get("animation_id", "")
    if not anim_id or not isinstance(anim_id, str):
        errors.append("animation_id must be a non-empty string")

    dur = mg_def.get("duration_sec", 0)
    if not isinstance(dur, (int, float)) or dur <= 0:
        errors.append("duration_sec must be a positive number")

    for dim in ("width", "height"):
        v = mg_def.get(dim, 0)
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append(f"{dim} must be a positive number")

    elements = mg_def.get("elements", [])
    if not isinstance(elements, list) or len(elements) == 0:
        errors.append("elements must be a non-empty list")
    else:
        for i, elem in enumerate(elements):
            elem_errors = _validate_element(elem, i, mg_def.get("duration_sec", 3.0))
            errors.extend(elem_errors)

    return len(errors) == 0, errors


def _validate_element(elem: dict, index: int, total_dur: float) -> list[str]:
    errors: list[str] = []
    prefix = f"elements[{index}]"

    elem_type = elem.get("type", "")
    if elem_type not in VALID_ELEMENT_TYPES:
        errors.append(f"{prefix}: type must be one of {VALID_ELEMENT_TYPES}, got '{elem_type}'")

    if elem_type == "text" and "content" not in elem:
        errors.append(f"{prefix}: text element missing 'content'")

    if elem_type == "shape":
        if "shape" not in elem:
            errors.append(f"{prefix}: shape element missing 'shape' field")
        elif elem.get("shape") not in VALID_SHAPES:
            errors.append(f"{prefix}: shape must be one of {VALID_SHAPES}")

    if elem_type == "image":
        # image 元素必须提供非空 src；x/y/width/height 必须为数值
        src = elem.get("src")
        if not isinstance(src, str) or not src.strip():
            errors.append(f"{prefix}: image element missing non-empty 'src'")
        for field in ("x", "y"):
            v = elem.get(field)
            if not isinstance(v, (int, float)):
                errors.append(f"{prefix}: image element '{field}' must be a number, got {v!r}")
        for field in ("width", "height"):
            v = elem.get(field)
            if not isinstance(v, (int, float)) or v <= 0:
                errors.append(f"{prefix}: image element '{field}' must be a positive number, got {v!r}")

    kfs = elem.get("keyframes", [])
    if not isinstance(kfs, list) or len(kfs) < 2:
        errors.append(f"{prefix}: must have at least 2 keyframes")
    else:
        for j, kf in enumerate(kfs):
            errors.extend(_validate_keyframe(kf, j, total_dur, prefix))

    for pos in ("x", "y"):
        val = elem.get(pos)
        if val is None:
            continue
        if isinstance(val, str) and val not in VALID_POSITIONS:
            try:
                float(val)
            except (ValueError, TypeError):
                errors.append(f"{prefix}: {pos} must be center/left/right/top/bottom or a number")

    return errors


def _validate_keyframe(kf: dict, index: int, total_dur: float, parent_prefix: str) -> list[str]:
    errors: list[str] = []
    pfx = f"{parent_prefix}.keyframes[{index}]"

    t = kf.get("time", -1)
    if not isinstance(t, (int, float)) or t < 0 or t > total_dur:
        errors.append(f"{pfx}: time must be between 0 and {total_dur}")

    props = kf.get("properties", kf)
    if not isinstance(props, dict):
        errors.append(f"{pfx}: must have properties dict")
        return errors

    anim_props = {k: v for k, v in props.items() if k != "time"}
    if not anim_props:
        errors.append(f"{pfx}: no animatable properties found")
    else:
        for prop_name, prop_val in anim_props.items():
            if prop_name not in ANIMATABLE_PROPS:
                errors.append(f"{pfx}: unknown property '{prop_name}'")
            elif prop_name == "easing":
                errors.extend(_validate_easing(prop_val, pfx))

    return errors


def _validate_easing(value: Any, pfx: str) -> list[str]:
    """校验 easing 取值：命名曲线或 cubic-bezier 数组。"""
    if isinstance(value, str):
        if value in VALID_EASINGS:
            return []
        # 允许带 cubic-bezier(...) 包装的命名曲线透传
        if value.startswith("cubic-bezier(") and value.endswith(")"):
            return []
        return [f"{pfx}: invalid easing '{value}', must be one of "
                f"{sorted(VALID_EASINGS)} or a cubic-bezier(x1,y1,x2,y2) array"]
    if (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(n, (int, float)) for n in value)
    ):
        return []
    return [f"{pfx}: invalid easing {value!r}, must be a named curve or "
            "[x1, y1, x2, y2] array"]


def repair_mg_json(mg_def: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """尝试修复常见的 MG JSON 错误。"""
    fixes: list[str] = []
    repaired = dict(mg_def)

    if "animation_id" not in repaired or not repaired["animation_id"]:
        import uuid
        repaired["animation_id"] = f"mg_generated_{uuid.uuid4().hex[:8]}"
        fixes.append("Added missing animation_id")

    if "duration_sec" not in repaired or not isinstance(repaired.get("duration_sec"), (int, float)):
        repaired["duration_sec"] = 3.0
        fixes.append("Set default duration_sec=3.0")

    for dim, default in (("width", 1920), ("height", 1080)):
        if dim not in repaired or not isinstance(repaired.get(dim), (int, float)):
            repaired[dim] = default
            fixes.append(f"Set default {dim}={default}")

    if "elements" not in repaired or not repaired["elements"]:
        fixes.append("No elements - cannot repair")
        return repaired, fixes

    if "style" not in repaired:
        repaired["style"] = {"background": "transparent", "font_family": "sans-serif"}

    if "params" not in repaired:
        params = {}
        for elem in repaired.get("elements", []):
            for field in ("content", "font_color", "color"):
                val = elem.get(field, "")
                if not isinstance(val, str):
                    continue
                if "{text}" in val:
                    params.setdefault("text", {"type": "string", "default": ""})
                if "{value}" in val:
                    params.setdefault("value", {"type": "string", "default": ""})
                if "{accent}" in val:
                    params.setdefault("accent", {"type": "string", "default": "#4f8cff"})
        if params:
            repaired["params"] = params

    for i, elem in enumerate(repaired.get("elements", [])):
        kfs = elem.get("keyframes", [])
        if not kfs:
            elem["keyframes"] = [
                {"time": 0, "opacity": 0},
                {"time": 0.5, "opacity": 1},
            ]
            fixes.append(f"elements[{i}]: added default keyframes")
            continue

        first_time = kfs[0].get("time", -1)
        if first_time > 0:
            kfs.insert(0, {"time": 0, "opacity": 0})
            fixes.append(f"elements[{i}]: added time=0 keyframe")

        for kf in kfs:
            if "properties" in kf and isinstance(kf["properties"], dict):
                for pk, pv in kf["properties"].items():
                    if pk not in kf:
                        kf[pk] = pv

            # 清理未知动画属性（LLM 偶发的多余字段），避免整个生成降级
            unknown = [k for k in kf if k != "time" and k not in ANIMATABLE_PROPS]
            if unknown:
                for k in unknown:
                    kf.pop(k, None)
                fixes.append(f"elements[{i}]: removed unknown keyframe props {unknown}")

    return repaired, fixes
