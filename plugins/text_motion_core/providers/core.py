"""text-motion-core 的 MGDef 构建实现（插件侧）。"""

from __future__ import annotations

import copy
from typing import Any

#: MG 支持的文字关键帧属性（MGRenderer 白名单）
_TEXT_CSS = {
    "font_size", "font_color", "font_weight", "font_italic", "letter_spacing",
    "line_height", "text_shadow", "text_stroke", "border_radius",
}


from clipwright.animation.text_primitives import (
    char_width as _char_w,
    color as _color,
    style_has_user_shadow as _style_has_user_shadow,
    text_shadow as _text_shadow,
    tokens as _tokens,
)


def build_text_mgdef(
    mg_def: dict[str, Any],
    *,
    text: str,
    style: dict[str, Any],
    motion: dict[str, Any],
    width: int,
    height: int,
    display_sec: float,
) -> dict[str, Any]:
    """返回新的 MGDef：文字/样式/动效全部转成 MG HTML 可渲染结构。

    不支持的样式必须显式拒绝，不能静默降级成白字。
    """
    if any(k in style for k in ("texture", "torn")):
        raise ValueError("文字动画 MG 路径不支持纹理/撕纸；请用 text_effect_card")
    out = copy.deepcopy(mg_def)
    out["duration_sec"] = round(max(0.3, float(display_sec)), 3)
    out["width"] = int(width)
    out["height"] = int(height)

    elements = [e for e in (out.get("elements") or []) if isinstance(e, dict)]
    text_elements = [e for e in elements if str(e.get("type")) == "text"]
    decorations = [e for e in elements if str(e.get("type")) != "text"]
    if not text_elements:
        text_elements = [{"type": "text", "content": text, "x": "center", "y": "center"}]

    # 文字内容以旁白/关键词为准，避免 LLM 的旧 content 覆盖
    main = text_elements[0]
    main["content"] = text
    fill_raw = style.get("fill")
    if isinstance(fill_raw, dict) and str(fill_raw.get("type")) == "gradient":
        colors = [_color(c, "#ffffff") for c in (fill_raw.get("colors") or [])]
        if len(colors) >= 2:
            main["text_gradient"] = True
            main["background"] = f"linear-gradient(90deg, {colors[0]}, {colors[1]})"
            main["font_color"] = colors[0]
        else:
            main["font_color"] = "#ffffff"
    else:
        main["font_color"] = _color(fill_raw, "#ffffff")
    main["font_size"] = max(8, float(style.get("font_size") or 56))
    main["font_weight"] = "bold" if str(style.get("font_weight") or "") == "bold" else "normal"
    main["font_italic"] = bool(style.get("font_italic"))
    main["letter_spacing"] = float(style.get("letter_spacing") or 0)
    main["line_height"] = float(style.get("line_height") or 1.25)
    main["text_shadow"] = _text_shadow(style)
    if float(style.get("stroke_width") or 0) > 0:
        main["text_stroke"] = (
            f"{float(style['stroke_width']):g}px {_color(style.get('stroke_color'), '#000000')}"
        )
    # 带底板（尤其蓝色方块）的文字默认加投影；否则文字与底板同亮度会糊在一起。
    # 用户显式给 shadow 时保留用户值。
    try:
        _user_shadow = (float(style.get("shadow_blur") or 0) > 0
                        or abs(float(style.get("shadow_y") or 0)) > 0)
    except (TypeError, ValueError):
        _user_shadow = False
    if bool(style.get("box")) and not _user_shadow:
        main["text_shadow"] = "0 4px 10px rgba(0,0,0,0.78)"
    main["text_align"] = str(style.get("text_align") or "center")
    main["x"] = "center"
    main["y"] = "center"
    if style.get("position") == "top":
        main["y"] = "18%"

    total_w = sum(_char_w(ch, main["font_size"]) for ch in text)
    motion_type = str(motion.get("type") or "fade_in").lower()
    reveal = max(0.1, float(motion.get("reveal_sec") or 0.6))
    if motion_type in ("typewriter", "word_by_word"):
        tokens = _tokens(text, motion_type == "word_by_word")
        total_w = sum(_char_w(ch, main["font_size"]) for ch in text)
        x = (width - total_w) / 2
        y = (height - float(main["font_size"])) / 2
        if style.get("position") == "top":
            y = height * 0.18
        main["keyframes"] = []
        for i, token in enumerate(tokens):
            el = copy.deepcopy(main)
            el["content"] = token
            el["x"] = round(x, 1)
            el["y"] = round(y, 1)
            el["text_align"] = "left"
            t0 = i * reveal / max(1, len(tokens) - 1)
            el["keyframes"] = [
                {"time": 0.0, "opacity": 0},
                {"time": round(max(0.01, t0), 3), "opacity": 0},
                {"time": round(min(display_sec, t0 + 0.12), 3), "opacity": 1},
                {"time": round(display_sec, 3), "opacity": 1},
            ]
            decorations.append(el)
            x += sum(_char_w(ch, main["font_size"]) for ch in token)
        elements = decorations
    else:
        kfs = [
            {"time": 0.0, "opacity": 0},
            {"time": round(reveal, 3), "opacity": 1},
            {"time": round(max(reveal + 0.1, display_sec - 0.2), 3), "opacity": 1},
            {"time": round(display_sec, 3), "opacity": 0},
        ]
        if motion_type == "slide_in":
            kfs = [
                {"time": 0.0, "opacity": 0, "translate_y": 80},
                {"time": round(reveal, 3), "opacity": 1, "translate_y": 0},
                {"time": round(max(reveal + 0.1, display_sec - 0.2), 3), "opacity": 1},
                {"time": round(display_sec, 3), "opacity": 0},
            ]
        elif motion_type == "scale_in":
            kfs = [
                {"time": 0.0, "opacity": 0, "scale": 0.7},
                {"time": round(reveal, 3), "opacity": 1, "scale": 1.0},
                {"time": round(max(reveal + 0.1, display_sec - 0.2), 3), "opacity": 1, "scale": 1.0},
                {"time": round(display_sec, 3), "opacity": 0, "scale": 1.0},
            ]
        main["keyframes"] = kfs
        elements = [main] + decorations

    # 扩展动效：glitch / neon / highlight
    if motion_type == "glitch":
        main["text_shadow"] = "3px 0 #ff2a50, -3px 0 #00e5ff, 0 0 14px #ffffff"
        main["keyframes"] = [
            {"time": 0.0, "opacity": 0, "translate_x": -8},
            {"time": 0.08, "opacity": 1, "translate_x": 6},
            {"time": 0.16, "opacity": 0.5, "translate_x": -4},
            {"time": 0.24, "opacity": 1, "translate_x": 0},
            {"time": round(max(0.4, display_sec - 0.2), 3), "opacity": 1, "translate_x": 0},
            {"time": round(display_sec, 3), "opacity": 0},
        ]
    elif motion_type == "neon":
        main["text_shadow"] = (
            f"0 0 6px #00e5ff, 0 0 14px #00e5ff, 0 0 28px #00e5ff, 0 0 42px #ff2a50"
        )
        main["keyframes"] = [
            {"time": 0.0, "opacity": 0},
            {"time": 0.2, "opacity": 1},
            {"time": round(max(0.4, display_sec - 0.2), 3), "opacity": 1},
            {"time": round(display_sec, 3), "opacity": 0},
        ]
    elif motion_type == "highlight":
        # 高亮文字必须和黄色条有分离感：默认给文字加一层向下投影。
        # 用户显式给了非零 shadow 时保留用户值，不被默认覆盖。
        try:
            _has_user_shadow = (float(style.get("shadow_blur") or 0) > 0
                                or abs(float(style.get("shadow_y") or 0)) > 0)
        except (TypeError, ValueError):
            _has_user_shadow = False
        if not _has_user_shadow:
            main["text_shadow"] = "0 4px 10px rgba(0,0,0,0.78)"
        # 黄色高亮条：置于文字下方，宽度从 0 扫到全文
        hl = {
            "type": "shape", "shape": "rect",
            "x": (width - total_w) / 2, "y": (height - main["font_size"]) / 2 + main["font_size"] * 0.55,
            "width": total_w, "height": main["font_size"] * 0.45,
            "background": "#ffd400cc", "border_radius": 3,
            "keyframes": [
                {"time": 0.0, "opacity": 0, "width": 0},
                {"time": round(reveal, 3), "opacity": 1, "width": total_w},
                {"time": round(display_sec, 3), "opacity": 1, "width": total_w},
            ],
        }
        decorations.append(hl)

    # 重建 element 列表：上面的 glitch/neon/highlight 可能往 decorations 追加了图层
    elements = decorations if motion_type in ("typewriter", "word_by_word") else [main] + decorations
    if motion_type == "highlight":
        elements = [e for e in elements if e.get("type") == "shape"] + [e for e in elements if e.get("type") != "shape"]

    # 圆角底板：前置 shape（MG renderer 支持 background/border_radius）
    if style.get("box"):
        pad_x = float(style.get("box_padding_x") or 18)
        pad_y = float(style.get("box_padding_y") or 8)
        text_w = sum(_char_w(ch, main["font_size"]) for ch in text) + pad_x * 2
        text_h = main["font_size"] + pad_y * 2
        bg = {
            "type": "shape", "shape": "rect",
            "x": (width - text_w) / 2, "y": (height - text_h) / 2,
            "width": text_w, "height": text_h,
            "background": _color(style.get("box_color"), "#000000aa"),
            "border_radius": float(style.get("box_radius") or 6),
            "keyframes": [
                {"time": 0.0, "opacity": 1},
                {"time": round(display_sec, 3), "opacity": 1},
            ],
        }
        elements = [bg] + elements

    # 重建时 base 里可能已有旧底板/旧装饰；再追加会叠出多个半透明黑块，
    # 后续 shape 盖在文字上导致文字发灰。按几何/颜色去重（文本元素不参与）。
    _seen: set[tuple] = set()
    _deduped: list[dict] = []
    for _el in elements:
        if not isinstance(_el, dict) or _el.get("type") == "text":
            _deduped.append(_el)
            continue
        _key = (
            str(_el.get("type")), str(_el.get("shape")),
            round(float(_el.get("x") or 0), 2), round(float(_el.get("y") or 0), 2),
            round(float(_el.get("width") or 0), 2), round(float(_el.get("height") or 0), 2),
            str(_el.get("background") or ""), str(_el.get("color") or ""),
            str((_el.get("props") or {}).get("color") or ""),
            str((_el.get("props") or {}).get("width") or ""),
            str((_el.get("props") or {}).get("height") or ""),
        )
        if _key in _seen:
            continue
        _seen.add(_key)
        _deduped.append(_el)
    out["elements"] = _deduped
    return out


def render_text_mg_html(mg_def: dict[str, Any], *, width: int, height: int,
                        fps: float = 30.0) -> str:
    from clipwright.animation.mg_renderer import MGRenderer
    return MGRenderer.render(mg_def, params={}, width=width, height=height, fps=fps)


class CoreMotionProvider:
    """一个基础动效 provider；id 决定 motion 语义。"""

    def __init__(self, motion_id: str, version: str = "1.0.0"):
        self.id = motion_id
        self.version = version

    def capabilities(self) -> dict:
        return {"motions": [self.id], "preview": "exact",
                "fidelity": "exact", "schema": 1,
                "required_style_fields": ["font_size", "fill", "text_shadow"]}

    def supports(self, *, text: str, style: dict, motion: dict) -> bool:
        return str(motion.get("type") or "") == self.id

    def plan(self, request):
        motion = dict(request.motion)
        motion["type"] = self.id
        mg_def = build_text_mgdef(
            request.base_mg_def or {}, text=request.text, style=request.style,
            motion=motion, width=request.width, height=request.height,
            display_sec=request.duration_sec,
        )
        from clipwright.animation.text_engine import MotionPlan
        return MotionPlan(mg_def=mg_def, provider_id=self.id,
                          provider_version=self.version, fidelity="exact", preview_ops=[])


def providers() -> list[CoreMotionProvider]:
    return [CoreMotionProvider(m) for m in (
        "fade_in", "fade_out", "slide_in", "scale_in",
        "typewriter", "word_by_word", "highlight", "glitch", "neon",
    )]
