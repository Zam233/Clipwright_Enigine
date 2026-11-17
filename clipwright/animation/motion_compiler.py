"""MotionSpec → MGDef 编译器（render 与 preview 共用）。"""

from __future__ import annotations

from typing import Any

from clipwright.animation.motion_interpolation import easing_progress, sample_path, sample_spring
from clipwright.animation.motion_spec import Easing, Keyframe, Layer, MotionSpec
from clipwright.animation.text_engine import MotionPlan


def _ease_string(easing: Any) -> str | None:
    if easing is None:
        return None
    if isinstance(easing, str):
        return easing
    if isinstance(easing, Easing):
        if easing.type == "named":
            return easing.name or "linear"
        if easing.type == "cubic-bezier":
            return f"cubic-bezier({easing.x1:g},{easing.y1:g},{easing.x2:g},{easing.y2:g})"
        if easing.type == "steps":
            return f"steps({easing.count},{easing.position})"
        return easing.type
    return None


def _keyframes(layer: Layer) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kf in layer.keyframes:
        d = kf.model_dump(exclude={"easing"}, exclude_none=True)
        es = _ease_string(kf.easing)
        if es:
            d["easing"] = es
        out.append(d)
    # spring 采样：Easing 为 spring 的区间在 compiler 中展开为离散帧
    if any(isinstance(k.easing, Easing) and k.easing.type == "spring" for k in layer.keyframes):
        # 对数值属性分别采样；不改变非数值属性
        n = 60
        sampled: list[dict[str, Any]] = []
        for i in range(n + 1):
            t = layer.keyframes[0].time + (layer.keyframes[-1].time - layer.keyframes[0].time) * i / n
            sampled.append({"time": round(t, 3)})
        for prop in ("opacity", "translate_x", "translate_y", "scale", "scale_x", "scale_y",
                     "rotate", "rotate_z", "blur"):
            seq = [(k.time, getattr(k, prop, None), k.easing) for k in layer.keyframes]
            seq = [x for x in seq if x[1] is not None]
            if len(seq) < 2:
                continue
            for i, f in enumerate(sampled):
                t = f["time"]
                a = seq[0]
                b = seq[-1]
                for j in range(len(seq) - 1):
                    if seq[j][0] <= t <= seq[j + 1][0]:
                        a, b = seq[j], seq[j + 1]
                        break
                span = max(1e-6, b[0] - a[0])
                local = (t - a[0]) / span
                eased = easing_progress(a[2], local) if a[2] else local
                f[prop] = float(a[1]) + (float(b[1]) - float(a[1])) * eased
        out = sampled
    return out


def compile_motion_spec(spec: MotionSpec, *, width: int | None = None, height: int | None = None,
                         fps: float | None = None) -> MotionPlan:
    w = int(width or spec.canvas.get("width") or 1920)
    h = int(height or spec.canvas.get("height") or 1080)
    duration = float(spec.duration_sec)
    elements: list[dict[str, Any]] = []

    for layer in sorted(spec.layers, key=lambda x: (x.z_index, x.id)):
        style = dict(layer.style or {})
        base: dict[str, Any] = {"type": layer.type, "id": layer.id}
        layout = layer.layout or {}
        base["x"] = layout.get("x", "center")
        base["y"] = layout.get("y", "center")
        if layer.type == "text":
            base["content"] = layer.content
            for src, dst in (
                ("font_size", "font_size"), ("font", "font_family"), ("font_weight", "font_weight"),
                ("font_italic", "font_italic"), ("letter_spacing", "letter_spacing"),
                ("line_height", "line_height"), ("fill", "font_color"), ("stroke_width", "stroke_width"),
                ("stroke_color", "stroke_color"), ("text_align", "text_align"),
            ):
                if src in style:
                    base[dst] = style[src]
            shadows = style.get("shadow")
            if isinstance(shadows, list) and shadows:
                s0 = shadows[0] if isinstance(shadows[0], dict) else {}
                base["text_shadow"] = (f"{s0.get('x', 0):g}px {s0.get('y', 0):g}px "
                                       f"{s0.get('blur', 0):g}px {s0.get('color', '#000000b3')}")
            if isinstance(style.get("gradient_stops"), list):
                stops = style["gradient_stops"]
                if len(stops) >= 2:
                    base["text_gradient"] = True
                    base["background"] = "linear-gradient(90deg," + ",".join(
                        f"{s.get('color', '#fff')} {float(s.get('offset', 0)) * 100:g}%" for s in stops
                    ) + ")"
            if isinstance(style.get("box"), dict):
                b = style["box"]
                base["background"] = b.get("color", "#12121a99")
                base["border_radius"] = b.get("radius", 8)
        elif layer.type in ("shape", "line"):
            base["shape"] = "rect" if layer.type == "shape" else "line"
            if "width" in style:
                base["width"] = style["width"]
            if "height" in style:
                base["height"] = style["height"]
            if "fill" in style:
                base["background"] = style["fill"]
                base["color"] = style["fill"]
            if "radius" in style:
                base["border_radius"] = style["radius"]
        elif layer.type == "image":
            base["src"] = style.get("src", "")
        else:
            continue
        kfs = _keyframes(layer)
        if layer.motion_path:
            # 路径采样为 translate_x/y（相对画布像素）
            n = max(8, int(duration * 30))
            prog = layer.motion_path.progress or [{"time": 0, "value": 0}, {"time": duration, "value": 1}]
            for i in range(n + 1):
                t = duration * i / n
                # 取 progress 中最近的线性值
                a = prog[0]
                b = prog[-1]
                for j in range(len(prog) - 1):
                    if float(prog[j].get("time", 0)) <= t <= float(prog[j + 1].get("time", 0)):
                        a, b = prog[j], prog[j + 1]
                        break
                span = max(1e-6, float(b.get("time", 0)) - float(a.get("time", 0)))
                local = (t - float(a.get("time", 0))) / span
                val = float(a.get("value", 0)) + (float(b.get("value", 1)) - float(a.get("value", 0))) * local
                px, py = sample_path(layer.motion_path.type, layer.motion_path.points, val)
                kfs.append({"time": round(t, 3),
                            "translate_x": (px - 0.5) * w, "translate_y": (py - 0.5) * h})
        if kfs:
            base["keyframes"] = kfs
        # effects → CSS 可接收的静态/关键帧字段
        for effect in layer.effects or []:
            if effect.get("type") == "blur" and isinstance(effect.get("radius"), (int, float)):
                base["filter"] = f"blur({effect['radius']:g}px)"
            if effect.get("type") == "glow" and effect.get("color"):
                base["text_shadow"] = f"0 0 {float(effect.get('blur', 14)):g}px {effect['color']}"
        elements.append(base)

    mg = {
        "animation_id": spec.spec_id or "motion_spec",
        "duration_sec": duration, "width": w, "height": h,
        "elements": elements,
    }
    return MotionPlan(mg_def=mg, provider_id="motion_spec", provider_version="1.0",
                      fidelity="exact", preview_ops=[{"kind": "mg_def", "fidelity": "exact"}])
