"""My Animation Pack Plugin.

Demonstrates registering custom onscreen + transition animations
via AnimationRegistry.
"""

from __future__ import annotations

from clipwright.animation import AnimationRegistry
from clipwright.plugins import CapabilityPlugin
from clipwright.schema.animation import (
    AnimationDef,
    AnimationTarget,
    AnimationType,
    EasingFunction,
    Keyframe,
)
from clipwright.schema.plugin import PluginManifest, PluginKind


class MyAnimationPackPlugin(CapabilityPlugin):
    """示例动画插件：注册 2 个自定义动画。"""

    manifest = PluginManifest(
        id="my_animations",
        name="My Animation Pack",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Custom animations: shake alert + radial wipe",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        # 1. Onscreen: 抖动警告
        AnimationRegistry.register(
            AnimationDef(
                animation_id="shake_alert",
                name="抖动警告",
                type=AnimationType.ONSCREEN,
                target=AnimationTarget.TEXT,
                duration_sec=0.5,
                easing=EasingFunction.LINEAR,
                keyframes=[
                    Keyframe(time=0.0, properties={"translate_x": 0, "opacity": 1}),
                    Keyframe(time=0.1, properties={"translate_x": -10, "opacity": 1}),
                    Keyframe(time=0.2, properties={"translate_x": 10, "opacity": 1}),
                    Keyframe(time=0.3, properties={"translate_x": -8, "opacity": 1}),
                    Keyframe(time=0.4, properties={"translate_x": 8, "opacity": 1}),
                    Keyframe(time=0.5, properties={"translate_x": 0, "opacity": 0}),
                ],
                properties_meta={
                    "translate_x": {
                        "type": "float", "default": 0, "range": [-100, 100], "unit": "percent"
                    },
                    "opacity": {
                        "type": "float", "default": 1, "range": [0, 1]
                    },
                },
            ),
            plugin_id=self.manifest.id,
        )

        # 2. Transition: 径向擦除
        AnimationRegistry.register(
            AnimationDef(
                animation_id="radial_wipe",
                name="径向擦除",
                type=AnimationType.TRANSITION,
                duration_sec=0.6,
                easing=EasingFunction.EASE_IN_OUT,
                ffmpeg_filter="",
                params={
                    "direction": {
                        "type": "string", "default": "clockwise",
                        "enum": ["clockwise", "counter"],
                        "description": "旋转方向"
                    },
                    "softness": {
                        "type": "float", "default": 0.2, "range": [0, 1],
                        "description": "边缘柔化"
                    },
                },
                description="以画面中心为圆点的径向擦除过渡",
            ),
            plugin_id=self.manifest.id,
        )

    def shutdown(self) -> None:
        pass


__all__ = ["MyAnimationPackPlugin"]
