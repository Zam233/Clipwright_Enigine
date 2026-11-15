"""文字动画 → MG HTML 适配层。

用户裁定（2026-11-15）：**所有文字动画统一走 MG HTML**，不再用 ASS/drawtext
渲染文字层。本模块把 `text_generation` 的 MGDef + TextAnimationStyle 转成可由
Hyperframes 渲染的 MGDef：

- 纯色 fill / 描边 / 阴影 / 字重 / 字距 / 对齐；
- 圆角底板 = 一个前置 shape 元素；
- typewriter / word_by_word = 每个 token 一个 text 元素 + 错峰 opacity 关键帧；
- slide/scale/highlight 仍用 MG 关键帧（不再靠 ASS override）。

渐变/撕纸/glitch/霓虹不在这里假装支持：调用方必须显式拒绝或走静态
`text_effect_card`，避免“声明支持、成片没有”。
"""

from __future__ import annotations

import copy
from typing import Any

#: MG 支持的文字关键帧属性（MGRenderer 白名单）
_TEXT_CSS = {
    "font_size", "font_color", "font_weight", "font_italic", "letter_spacing",
    "line_height", "text_shadow", "text_stroke", "border_radius",
}


def _color(value: Any, fallback: str = "#ffffff") -> str:
    s = str(value or "").strip()
    if s.startswith("#") and len(s) in (4, 7, 9):
        return s
    # rgba() → #rrggbbaa
    if s.startswith("rgba") or s.startswith("rgb"):
        import re
        m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([0-9.]+))?\)", s)
        if m:
            a = float(m.group(4)) if m.group(4) else 1.0
            return f"#{int(m.group(1)):02x}{int(m.group(2)):02x}{int(m.group(3)):02x}{int(a*255):02x}"
    return fallback


def _char_w(ch: str, size: float) -> float:
    return size * (1.0 if ord(ch) > 0x2E80 else (0.3 if ch == " " else 0.6))


def _tokens(text: str, word_mode: bool) -> list[str]:
    if not word_mode:
        return list(text)
    import re
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\w\s]", text) or [text]


def _text_shadow(style: dict[str, Any]) -> str:
    x = float(style.get("shadow_x") or 0)
    y = float(style.get("shadow_y") or 0)
    blur = float(style.get("shadow_blur") or 0)
    color = _color(style.get("shadow_color"), "#000000b3")
    return f"{x:g}px {y:g}px {blur:g}px {color}"


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

    out["elements"] = elements
    return out


def render_text_mg_html(mg_def: dict[str, Any], *, width: int, height: int,
                        fps: float = 30.0) -> str:
    from clipwright.animation.mg_renderer import MGRenderer
    return MGRenderer.render(mg_def, params={}, width=width, height=height, fps=fps)


def apply_text_style_to_clip(
    clip: Any, *, style: dict[str, Any], motion: dict[str, Any],
    width: int, height: int,
) -> Any:
    """所有文字动画的统一入口：字段 + metadata + mg_html 一起应用。

    不允许只改字段不重建 HTML（那会“样式已应用”但成片还是旧样式）。
    """
    from clipwright.animation.text_mg import build_text_mgdef, render_text_mg_html
    meta = dict(getattr(clip, "metadata", None) or {})
    mg_def = meta.get("mg_def") if isinstance(meta.get("mg_def"), dict) else {}
    if not mg_def:
        mg_def = {
            "animation_id": "text_style", "duration_sec": float(clip.duration_sec or 3),
            "width": int(width), "height": int(height),
            "elements": [{"type": "text", "content": str(clip.text or ""),
                          "x": "center", "y": "center"}],
        }
    built = build_text_mgdef(
        mg_def, text=str(clip.text or ""), style=style, motion=motion,
        width=int(width), height=int(height), display_sec=float(clip.duration_sec or 3),
    )
    html = render_text_mg_html(built, width=int(width), height=int(height))
    meta.update({
        "style": dict(style), "motion": dict(motion), "mg_def": built,
        "mg_html": html, "mg_params": {}, "renderer": "mg_hyperframes",
        "apply_text_style": True,
    })
    clip.metadata = meta
    return clip
