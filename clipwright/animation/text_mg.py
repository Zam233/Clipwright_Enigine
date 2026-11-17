"""文字动画 engine 兼容层。

具体 motion（fade/slide/scale/typewriter/word_by_word/highlight/glitch/neon）
已经迁移到 `plugins/text_motion_core`；本模块只保留 engine 级兼容入口和
渲染原语别名，**不得再新增任何 motion 分支**。
"""

from __future__ import annotations

from typing import Any

from clipwright.animation.text_primitives import (
    caption_band_px,
    char_width,
    color,
    style_has_user_shadow,
    text_shadow,
    tokens,
)


def render_text_mg_html(mg_def: dict[str, Any], *, width: int, height: int,
                        fps: float = 30.0) -> str:
    from clipwright.animation.mg_renderer import MGRenderer
    return MGRenderer.render(mg_def, params={}, width=width, height=height, fps=fps)


def apply_text_style_to_clip(
    clip: Any, *, style: dict[str, Any], motion: dict[str, Any],
    width: int, height: int,
) -> Any:
    """兼容 wrapper：统一委托 TextAnimationEngine（motion 由插件提供）。"""
    from clipwright.animation.text_engine import get_engine
    return get_engine().apply_style(clip, style=style, motion=motion,
                                    width=width, height=height)


__all__ = [
    "apply_text_style_to_clip", "render_text_mg_html",
    "caption_band_px", "char_width", "color", "style_has_user_shadow",
    "text_shadow", "tokens",
]
