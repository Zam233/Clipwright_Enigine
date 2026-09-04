"""公共 xfade 转场映射（C4）。

EditAgent 的 LLM 剪辑档案与 AnimationAgent 的转场编目使用不同命名空间
（如 LLM 输出 crossfade/slide_left/zoom_in，ffmpeg xfade 只认 fade/slideleft/zoomin）。
此前 EditAgent 把 LLM 语义名原样写入 transition_in，render 收到非法 xfade 名
EINVAL 后静默降级硬切——LLM 的转场决策从未生效。

统一映射表供两处复用；render 白名单校验使用 XFADE_VALUES 精确集合。
"""

from __future__ import annotations

# 语义/编目转场名 → ffmpeg xfade transition 值
XFADE_MAP: dict[str, str] = {
    "crossfade": "fade",
    "dissolve": "dissolve",
    "fade": "fade",
    "fade_to_black": "fadeblack",
    "push_left": "pushleft",
    "push_right": "pushright",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "wipe_up": "wipeup",
    "wipe_down": "wipedown",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "slide_up": "slideup",
    "slide_down": "slidedown",
    "zoom_in": "zoomin",
    "glitch": "fade",
    "pixel_dissolve": "pixelize",
    "cut": "fade",
}

# render 白名单：合法 xfade transition 值（含映射目标值）
XFADE_VALUES: frozenset[str] = frozenset(XFADE_MAP.values())


def to_xfade(anim_id: str) -> str:
    """语义转场名 → ffmpeg xfade 值；未知名降级 fade。"""
    return XFADE_MAP.get(anim_id or "", "fade")
