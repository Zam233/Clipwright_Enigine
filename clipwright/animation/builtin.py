"""内置动画定义 — 开箱即用的屏幕动画和转场动画。

所有定义通过 register_builtin_animations() 注册到 AnimationRegistry。
"""

from __future__ import annotations

from clipwright.schema.animation import (
    AnimationDef,
    AnimationTarget,
    AnimationType,
    EasingFunction,
    Keyframe,
    PropertyDef,
)


# ── 屏幕上展示的动画 ──────────────────────────────────

ONSCREEN_ANIMATIONS: list[AnimationDef] = [
    # ── 淡入系 ──
    AnimationDef(
        animation_id="fade_in",
        name="淡入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0}),
            Keyframe(time=1.0, properties={"opacity": 1}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1], description="不透明度"),
        },
    ),
    AnimationDef(
        animation_id="fade_out",
        name="淡出",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.5,
        easing=EasingFunction.EASE_IN,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 1}),
            Keyframe(time=1.0, properties={"opacity": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=0, range=[0, 1], description="不透明度"),
        },
    ),
    # ── 滑动系 ──
    AnimationDef(
        animation_id="slide_up_in",
        name="上滑进入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.6,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "translate_y": 60}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_y": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "translate_y": PropertyDef(type="float", default=0, range=[-200, 200], unit="percent"),
        },
    ),
    AnimationDef(
        animation_id="slide_down_out",
        name="下滑退出",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.5,
        easing=EasingFunction.EASE_IN_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 1, "translate_y": 0}),
            Keyframe(time=1.0, properties={"opacity": 0, "translate_y": 60}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=0, range=[0, 1]),
            "translate_y": PropertyDef(type="float", default=60, range=[-200, 200], unit="percent"),
        },
    ),
    AnimationDef(
        animation_id="slide_left_in",
        name="左滑进入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "translate_x": -80}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_x": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "translate_x": PropertyDef(type="float", default=0, range=[-200, 200], unit="percent"),
        },
    ),
    # ── 滑动系（旧 fallback 广告名，注册使其可被 marker 解析）──
    AnimationDef(
        animation_id="slide_down",
        name="下滑",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "translate_y": -30}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_y": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "translate_y": PropertyDef(type="float", default=0, range=[-200, 200], unit="percent"),
        },
    ),
    AnimationDef(
        animation_id="slide_left",
        name="左滑",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "translate_x": 50}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_x": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "translate_x": PropertyDef(type="float", default=0, range=[-200, 200], unit="percent"),
        },
    ),
    AnimationDef(
        animation_id="slide_right",
        name="右滑",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "translate_x": -50}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_x": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "translate_x": PropertyDef(type="float", default=0, range=[-200, 200], unit="percent"),
        },
    ),
    # ── 震动 ──
    AnimationDef(
        animation_id="shake",
        name="震动",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.TEXT,
        duration_sec=0.4,
        easing=EasingFunction.EASE_IN_OUT,
        keyframes=[
            Keyframe(time=0.0, properties={"translate_x": 0}),
            Keyframe(time=0.2, properties={"translate_x": -4}),
            Keyframe(time=0.4, properties={"translate_x": 4}),
            Keyframe(time=0.6, properties={"translate_x": -2}),
            Keyframe(time=0.8, properties={"translate_x": 0}),
        ],
        properties_meta={
            "translate_x": PropertyDef(type="float", default=0, range=[-200, 200], unit="percent"),
        },
    ),
    # ── 缩放系 ──
    AnimationDef(
        animation_id="scale_in",
        name="缩放进入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.4,
        easing=EasingFunction.EASE_OUT_QUAD,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "scale_x": 0.5, "scale_y": 0.5}),
            Keyframe(time=1.0, properties={"opacity": 1, "scale_x": 1.0, "scale_y": 1.0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "scale_x": PropertyDef(type="float", default=1, range=[0, 10]),
            "scale_y": PropertyDef(type="float", default=1, range=[0, 10]),
        },
    ),
    AnimationDef(
        animation_id="scale_bounce",
        name="弹跳进入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.8,
        easing=EasingFunction.EASE_OUT_ELASTIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "scale_x": 0.0, "scale_y": 0.0}),
            Keyframe(time=0.6, properties={"opacity": 1, "scale_x": 1.1, "scale_y": 1.1}),
            Keyframe(time=1.0, properties={"opacity": 1, "scale_x": 1.0, "scale_y": 1.0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "scale_x": PropertyDef(type="float", default=1, range=[0, 10]),
            "scale_y": PropertyDef(type="float", default=1, range=[0, 10]),
        },
    ),
    # ── 模糊入场 ──
    AnimationDef(
        animation_id="blur_in",
        name="模糊进入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.8,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "blur": 12}),
            Keyframe(time=0.3, properties={"opacity": 0, "blur": 12}),
            Keyframe(time=1.0, properties={"opacity": 1, "blur": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "blur": PropertyDef(type="float", default=0, range=[0, 50], unit="px"),
        },
    ),
    # ── 旋转入场 ──
    AnimationDef(
        animation_id="rotate_in",
        name="旋转进入",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.7,
        easing=EasingFunction.EASE_OUT_QUART,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "rotate": -45, "scale_x": 0.6, "scale_y": 0.6}),
            Keyframe(time=0.5, properties={"opacity": 1, "rotate": 5, "scale_x": 1.05, "scale_y": 1.05}),
            Keyframe(time=1.0, properties={"opacity": 1, "rotate": 0, "scale_x": 1.0, "scale_y": 1.0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "rotate": PropertyDef(type="float", default=0, range=[-360, 360], unit="degrees"),
            "scale_x": PropertyDef(type="float", default=1, range=[0, 10]),
            "scale_y": PropertyDef(type="float", default=1, range=[0, 10]),
        },
    ),
    # ── 强调系 ──
    AnimationDef(
        animation_id="pulse",
        name="脉冲强调",
        type=AnimationType.ONSCREEN,
        target=AnimationTarget.ANY,
        duration_sec=0.6,
        easing=EasingFunction.EASE_IN_OUT,
        keyframes=[
            Keyframe(time=0.0, properties={"scale_x": 1.0, "scale_y": 1.0, "opacity": 1}),
            Keyframe(time=0.3, properties={"scale_x": 1.2, "scale_y": 1.2, "opacity": 0.8}),
            Keyframe(time=0.6, properties={"scale_x": 1.0, "scale_y": 1.0, "opacity": 1}),
        ],
        properties_meta={
            "scale_x": PropertyDef(type="float", default=1.2, range=[0, 10]),
            "scale_y": PropertyDef(type="float", default=1.2, range=[0, 10]),
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
        },
    ),
]

# ── 文字动画 ──────────────────────────────────────────

TEXT_ANIMATIONS: list[AnimationDef] = [
    AnimationDef(
        animation_id="typewriter",
        name="打字",
        type=AnimationType.TEXT,
        duration_sec=1.0,
        easing=EasingFunction.LINEAR,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 1, "char_progress": 0}),
            Keyframe(time=1.0, properties={"opacity": 1, "char_progress": 1}),
        ],
        properties_meta={
            "char_progress": PropertyDef(type="float", default=1, range=[0, 1], description="字符逐显进度"),
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
        },
    ),
    AnimationDef(
        animation_id="highlight_flash",
        name="高亮",
        type=AnimationType.TEXT,
        duration_sec=0.3,
        easing=EasingFunction.LINEAR,
        keyframes=[
            Keyframe(time=0.0, properties={"color_emphasis": 0}),
            Keyframe(time=0.5, properties={"color_emphasis": 1}),
            Keyframe(time=1.0, properties={"color_emphasis": 0}),
        ],
        properties_meta={
            "color_emphasis": PropertyDef(type="float", default=1, range=[0, 1], description="强调色强度"),
        },
    ),
    AnimationDef(
        animation_id="text_fade_in",
        name="淡入",
        type=AnimationType.TEXT,
        duration_sec=0.4,
        easing=EasingFunction.EASE_OUT,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0}),
            Keyframe(time=1.0, properties={"opacity": 1}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
        },
    ),
    AnimationDef(
        animation_id="text_slide_up",
        name="滑入",
        type=AnimationType.TEXT,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT_CUBIC,
        keyframes=[
            Keyframe(time=0.0, properties={"opacity": 0, "translate_y": 20}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_y": 0}),
        ],
        properties_meta={
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
            "translate_y": PropertyDef(type="float", default=0, range=[-100, 100], unit="percent"),
        },
    ),
    AnimationDef(
        animation_id="char_by_char",
        name="逐字",
        type=AnimationType.TEXT,
        duration_sec=1.2,
        easing=EasingFunction.LINEAR,
        keyframes=[
            Keyframe(time=0.0, properties={"char_progress": 0, "opacity": 1}),
            Keyframe(time=0.2, properties={"char_progress": 0.2, "opacity": 1}),
            Keyframe(time=0.5, properties={"char_progress": 0.5, "opacity": 1}),
            Keyframe(time=0.8, properties={"char_progress": 0.8, "opacity": 1}),
            Keyframe(time=1.0, properties={"char_progress": 1.0, "opacity": 1}),
        ],
        properties_meta={
            "char_progress": PropertyDef(type="float", default=1, range=[0, 1], description="字符进度"),
            "opacity": PropertyDef(type="float", default=1, range=[0, 1]),
        },
    ),
]

# ── 转场动画 ──────────────────────────────────────────

TRANSITION_ANIMATIONS: list[AnimationDef] = [
    AnimationDef(
        animation_id="cut",
        name="硬切",
        type=AnimationType.TRANSITION,
        duration_sec=0.0,
        easing=EasingFunction.LINEAR,
        ffmpeg_filter="",
        description="瞬间切换，无过渡效果",
    ),
    AnimationDef(
        animation_id="crossfade",
        name="淡入淡出",
        type=AnimationType.TRANSITION,
        duration_sec=0.5,
        easing=EasingFunction.EASE_IN_OUT,
        ffmpeg_filter="crossfade=d={duration}:first_pts=0",
        params={
            "softness": PropertyDef(type="float", default=0.3, range=[0, 1], description="过渡柔和度"),
        },
        description="前片段渐隐同时后片段渐显",
    ),
    AnimationDef(
        animation_id="fade_to_black",
        name="黑场过渡",
        type=AnimationType.TRANSITION,
        duration_sec=0.4,
        easing=EasingFunction.EASE_IN,
        ffmpeg_filter="fade=t=out:st=0:d={duration},fade=t=in:st={duration}:d={duration}",
        description="先黑再亮",
    ),
    AnimationDef(
        animation_id="push_left",
        name="左推",
        type=AnimationType.TRANSITION,
        duration_sec=0.4,
        easing=EasingFunction.EASE_IN_OUT,
        ffmpeg_filter="",
        description="后片段从左向右推入覆盖前片段",
    ),
    AnimationDef(
        animation_id="push_right",
        name="右推",
        type=AnimationType.TRANSITION,
        duration_sec=0.4,
        easing=EasingFunction.EASE_IN_OUT,
        ffmpeg_filter="",
        description="后片段从右向左推入覆盖前片段",
    ),
    AnimationDef(
        animation_id="wipe_left",
        name="左擦除",
        type=AnimationType.TRANSITION,
        duration_sec=0.4,
        easing=EasingFunction.EASE_IN_OUT,
        ffmpeg_filter="",
        params={
            "softness": PropertyDef(type="float", default=0.2, range=[0, 1], description="擦除边缘柔化"),
        },
        description="从左到右擦除",
    ),
    AnimationDef(
        animation_id="zoom_in",
        name="放大进入",
        type=AnimationType.TRANSITION,
        duration_sec=0.5,
        easing=EasingFunction.EASE_OUT_CUBIC,
        ffmpeg_filter="",
        description="后片段由小变大进入",
    ),
    AnimationDef(
        animation_id="glitch",
        name="故障干扰",
        type=AnimationType.TRANSITION,
        duration_sec=0.3,
        easing=EasingFunction.LINEAR,
        ffmpeg_filter="",
        params={
            "intensity": PropertyDef(type="float", default=0.15, range=[0, 0.5], description="干扰强度"),
            "segments": PropertyDef(type="int", default=3, range=[1, 10], description="噪点分段数"),
        },
        description="数字故障风格的快速转场",
    ),
    AnimationDef(
        animation_id="pixel_dissolve",
        name="像素溶解",
        type=AnimationType.TRANSITION,
        duration_sec=0.5,
        easing=EasingFunction.EASE_IN_OUT,
        ffmpeg_filter="",
        params={
            "block_size": PropertyDef(type="int", default=4, range=[1, 32], description="像素块大小"),
        },
        description="画面破碎成像素块溶解",
    ),
    AnimationDef(
        animation_id="slide_up",
        name="上滑",
        type=AnimationType.TRANSITION,
        duration_sec=0.4,
        easing=EasingFunction.EASE_IN_OUT,
        ffmpeg_filter="",
        description="后片段从下方滑入",
    ),
]


def register_builtin_animations() -> None:
    """注册所有内置动画到 AnimationRegistry。"""
    from clipwright.animation.registry import AnimationRegistry

    for defn in ONSCREEN_ANIMATIONS + TEXT_ANIMATIONS + TRANSITION_ANIMATIONS:
        AnimationRegistry.register(defn)
