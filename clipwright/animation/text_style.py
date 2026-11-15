"""文字动画样式契约（PRD docs/text-animation-style-prd.md M1）。

文字动画不是“白色 drawtext”，而是一层带排版/底板/描边/阴影/发光/动效的视觉层。
本模块只负责**数据契约与校验**，不负责渲染；渲染端按 `style` 字段映射到 ASS /
drawtext。LLM 输出顶层 `text_animation`：

    {
      "text_animation": {
        "style": {...},
        "motion": {"type": "typewriter", "reveal_sec": 1.2, "cursor": true},
        "display_sec": 3.2,
        "duration_reason": "短促强调，2 行阅读时间"
      }
    }

缺失时按旧 MGDef 元素推断（legacy），保证既有片段仍能出片；校验会把
`duration_reason`/`motion` 缺失记为错误，由生成端重试修复。
"""

from __future__ import annotations

import re
from typing import Any

#: 动效白名单（一期；渲染端不支持时降级为静态 + fidelity）
MOTION_TYPES: tuple[str, ...] = (
    "fade_in", "fade_out", "slide_in", "scale_in", "typewriter",
    "word_by_word", "highlight", "glitch", "neon",
)

#: 不必强制 reveal_sec 的动效
_TYPEWRITER_MOTIONS = {"typewriter", "word_by_word"}

DEFAULT_STYLE: dict[str, Any] = {
    "font": "sans-serif",
    "font_size": 56.0,
    "font_weight": "bold",
    "font_italic": False,
    "letter_spacing": 0.0,
    "line_height": 1.25,
    "text_align": "center",
    "fill": "#ffffff",
    "box": False,
    "box_color": "rgba(0,0,0,0.55)",
    "box_radius": 6.0,
    "box_padding_x": 18.0,
    "box_padding_y": 8.0,
    "stroke_width": 2.0,
    "stroke_color": "rgba(0,0,0,0.9)",
    "shadow_x": 0.0,
    "shadow_y": 4.0,
    "shadow_blur": 12.0,
    "shadow_color": "rgba(0,0,0,0.7)",
    "glow_color": "",
    "glow_width": 0.0,
    "position": "center",
    "offset_x": 0.0,
    "offset_y": 0.0,
    "avoid_caption_band": True,
}

_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}"
    r"(?:\s*,\s*[0-9.]+)?\s*\)|transparent)$"
)


def _num(value: Any, default: float, low: float, high: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return float(default)
    return round(max(low, min(high, n)), 3)


def _color(value: Any, default: str) -> str:
    s = str(value or "").strip()
    if not s:
        return default
    # 允许 {accent} 这类占位符，由 params 解析后再校验颜色
    if s.startswith("{") and s.endswith("}"):
        return s
    return s if _COLOR_RE.match(s) else default


def resolve_style_placeholders(style: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    """把 style 里的 `{accent}` 等占位符用 mg_def.params 解析。

    LLM 的 style 可能引用它自己声明的参数（例如 fill: "{accent}"）；
    不解析就会把字面量写进 ASS，libass 渲染成无色/黑块。
    """
    p = dict(params or {})
    if not p:
        return dict(style or {})
    def _one(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("{") and v.endswith("}") and len(v) > 2:
            key = v[1:-1].strip()
            return p.get(key, v)
        return v
    return {k: _one(v) for k, v in (style or {}).items()}


def normalize_style(raw: dict[str, Any] | None) -> dict[str, Any]:
    """合并默认值 + 夹取范围；非法颜色/枚举回落到安全默认。"""
    src = dict(raw or {})
    out = dict(DEFAULT_STYLE)
    out.update({
        "font": str(src.get("font") or out["font"]),
        "font_size": _num(src.get("font_size"), out["font_size"], 8, 240),
        "font_weight": "bold" if str(src.get("font_weight") or "").lower() == "bold" else "normal",
        "font_italic": bool(src.get("font_italic", out["font_italic"])),
        "letter_spacing": _num(src.get("letter_spacing"), out["letter_spacing"], -10, 80),
        "line_height": _num(src.get("line_height"), out["line_height"], 0.8, 3.0),
        "text_align": str(src.get("text_align") or out["text_align"])
        if str(src.get("text_align") or "") in ("left", "center", "right") else out["text_align"],
        "fill": _color(src.get("fill") or src.get("font_color"), out["fill"]),
        "box": bool(src.get("box", out["box"])),
        "box_color": _color(src.get("box_color"), out["box_color"]),
        "box_radius": _num(src.get("box_radius"), out["box_radius"], 0, 80),
        "box_padding_x": _num(src.get("box_padding_x"), out["box_padding_x"], 0, 160),
        "box_padding_y": _num(src.get("box_padding_y"), out["box_padding_y"], 0, 160),
        "stroke_width": _num(src.get("stroke_width"), out["stroke_width"], 0, 30),
        "stroke_color": _color(src.get("stroke_color"), out["stroke_color"]),
        "shadow_x": _num(src.get("shadow_x"), out["shadow_x"], -60, 60),
        "shadow_y": _num(src.get("shadow_y"), out["shadow_y"], -60, 60),
        "shadow_blur": _num(src.get("shadow_blur"), out["shadow_blur"], 0, 80),
        "shadow_color": _color(src.get("shadow_color"), out["shadow_color"]),
        "glow_color": _color(src.get("glow_color"), ""),
        "glow_width": _num(src.get("glow_width"), 0, 0, 40),
        "position": str(src.get("position") or out["position"])
        if str(src.get("position") or "") in ("center", "top", "bottom") else out["position"],
        "offset_x": _num(src.get("offset_x"), out["offset_x"], -2000, 2000),
        "offset_y": _num(src.get("offset_y"), out["offset_y"], -2000, 2000),
        "avoid_caption_band": bool(src.get("avoid_caption_band", True)),
    })
    # 文字动画一律避开底部字幕带：bottom 不是合法锚点
    if out["position"] == "bottom":
        out["position"] = "center"
    return out


def normalize_motion(raw: dict[str, Any] | None, *, text: str) -> dict[str, Any]:
    src = dict(raw or {})
    mtype = str(src.get("type") or src.get("motion") or "fade_in").strip().lower()
    if mtype not in MOTION_TYPES:
        mtype = "fade_in"
    n = max(1, len(str(text or "")))
    default_reveal = round(min(2.0, max(0.6, n * 0.08)), 3)
    reveal = _num(src.get("reveal_sec"), default_reveal, 0.1, 12)
    return {
        "type": mtype,
        "reveal_sec": reveal,
        "cursor": bool(src.get("cursor", mtype == "typewriter")),
        "stagger_sec": _num(src.get("stagger_sec"), 0.0, 0.0, 1.0),
        "easing": str(src.get("easing") or "ease-out"),
    }


def normalize_display(raw: dict[str, Any] | None, *, host_duration: float,
                      text: str) -> tuple[float, str]:
    """返回 (display_sec, duration_reason)。

    时长由内容决定，**不设硬上限**；只保证有限、>0、不超过宿主片段（超出会覆盖
    下一场景）。没有 reason 的返回空串，由校验/重试要求模型补上。
    """
    src = dict(raw or {})
    reason = str(src.get("duration_reason") or "").strip()
    try:
        display = float(src.get("display_sec"))
    except (TypeError, ValueError):
        display = 0.0
    if display <= 0:
        # 旧产物没给 display_sec：按阅读时间给一个保守值，但必须写 reason
        n = max(1, len(str(text or "")))
        display = min(max(1.5, 0.6 + n * 0.18), max(1.5, float(host_duration)))
        reason = reason or "legacy：按字数估算阅读时间（模型未给 duration_reason）"
    display = round(min(max(0.5, display), max(0.5, float(host_duration))), 3)
    return display, reason


def validate_style(style: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(style, dict):
        return ["text_animation.style 必须是对象"]
    for key in ("fill", "stroke_color", "shadow_color"):
        if not _COLOR_RE.match(str(style.get(key) or "")):
            # 占位符允许，但必须由 params 解析；其它非法值报错
            if not str(style.get(key) or "").startswith("{"):
                errors.append(f"style.{key} 不是合法颜色: {style.get(key)!r}")
    if float(style.get("stroke_width") or 0) < 0:
        errors.append("style.stroke_width < 0")
    if str(style.get("position") or "") == "bottom":
        errors.append("style.position=bottom 会压字幕带")
    return errors


def validate_motion(motion: dict[str, Any], *, text: str) -> list[str]:
    errors: list[str] = []
    mtype = str(motion.get("type") or "")
    if mtype not in MOTION_TYPES:
        errors.append(f"motion.type 非法: {mtype!r}")
    if mtype in _TYPEWRITER_MOTIONS:
        if float(motion.get("reveal_sec") or 0) <= 0:
            errors.append("typewriter/word_by_word 必须有 reveal_sec > 0")
        if mtype == "typewriter" and len(str(text or "")) > 40:
            errors.append("typewriter 文本超过 40 字，应交给字幕")
    return errors


def ensure_text_animation(mg_def: dict[str, Any], *, text: str,
                          host_duration: float) -> dict[str, Any]:
    """补齐/归一化 `text_animation`，返回稳定结构。

    优先使用模型显式字段；缺失时从主元素（字号最大）推断 legacy 样式，保证
    旧调用仍可出片，并把 `legacy=True` 暴露给质量/日志。
    """
    ta = dict(mg_def.get("text_animation") or {})
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    main = max(
        elements,
        key=lambda e: float(e.get("font_size") or 0),
        default={},
    ) if elements else {}
    legacy = not ta

    style_src = dict(ta.get("style") or {})
    if not style_src and main:
        style_src = {
            "font_size": main.get("font_size"),
            "fill": main.get("font_color") or main.get("color"),
            "font_weight": "bold" if str(main.get("font_weight") or "").lower() == "bold" else None,
            "stroke_width": main.get("border_width") if "text" not in str(main.get("border_color") or "") else 0,
            "stroke_color": main.get("border_color"),
            "letter_spacing": main.get("letter_spacing"),
            "text_align": main.get("align") or main.get("text_align"),
        }
    style = normalize_style(resolve_style_placeholders(style_src, mg_def.get("params") or {}))
    motion = normalize_motion(ta.get("motion") or {}, text=text)
    display, reason = normalize_display(ta, host_duration=host_duration, text=text)
    result = {
        "style": style,
        "motion": motion,
        "display_sec": display,
        "duration_reason": reason,
        "legacy": bool(legacy),
    }
    mg_def["text_animation"] = result
    return result
