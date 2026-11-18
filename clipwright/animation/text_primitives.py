"""文字动画渲染原语（engine-only）。

这里只提供**与具体 motion 无关**的能力：颜色/测量/token/阴影/字幕带查询。
任何 fade/typewriter/slide 等 motion 语义都不得写在本模块。
"""

from __future__ import annotations

import re
from typing import Any


def color(value: Any, fallback: str = "#ffffff") -> str:
    s = str(value or "").strip()
    if s.startswith("#") and len(s) in (4, 7, 9):
        return s
    if s.startswith("rgba") or s.startswith("rgb"):
        m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([0-9.]+))?\)", s)
        if m:
            a = float(m.group(4)) if m.group(4) else 1.0
            return f"#{int(m.group(1)):02x}{int(m.group(2)):02x}{int(m.group(3)):02x}{int(a * 255):02x}"
    return fallback


def char_width(ch: str, size: float) -> float:
    if ord(ch) > 0x2E80:
        return float(size)
    if ch == " ":
        return float(size) * 0.35
    return float(size) * 0.95


def tokens(text: str, word_mode: bool) -> list[str]:
    if not word_mode:
        return list(text)
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\w\s]", text) or [text]


def text_shadow(style: dict[str, Any]) -> str:
    x = float(style.get("shadow_x") or 0)
    y = float(style.get("shadow_y") or 0)
    blur = float(style.get("shadow_blur") or 0)
    c = color(style.get("shadow_color"), "#000000b3")
    return f"{x:g}px {y:g}px {blur:g}px {c}"


def style_has_user_shadow(style: dict[str, Any]) -> bool:
    try:
        return (float(style.get("shadow_blur") or 0) > 0
                or abs(float(style.get("shadow_y") or 0)) > 0)
    except (TypeError, ValueError):
        return False


def caption_band_px() -> int:
    # 与 text_generation.CAPTION_BAND_PX 保持一致；延迟 import 避免循环依赖
    try:
        from clipwright.animation.text_generation import CAPTION_BAND_PX
        return int(CAPTION_BAND_PX)
    except Exception:  # noqa: BLE001
        return 160
