"""动态文字排版插件 — 高级 Kinetic Typography 动画（能力方：插件）。

2026-11-08 审计整改（B8-T8.3）：旧实现有三处失效——
1. 声明了一个 engine 从不读取的目录扩展钩子（死钩子）；
2. 动画的 CSS 字符串没人消费（`AnimationDef` 没有 css 字段，渲染走 keyframes）；
3. `gradient_fill` / `perspective_3d` 依赖的 CSS 能力（渐变文字、3D 透视）在
   keyframes 渲染器里没有对应属性，属于"声明了但渲不出来"——按审计结论下线。

现在只保留 4 个能用 keyframes 表达的动画，每个都有非空 `keyframes` + `properties_meta`。
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.animation import (
    AnimationDef,
    AnimationTarget,
    AnimationType,
    EasingFunction,
    Keyframe,
    PropertyDef,
)
from clipwright.schema.plugin import PluginManifest, PluginKind

#: 动画定义（id → 元数据）。CSS 已删除：渲染只消费 keyframes。
KINETIC_ANIMATIONS: dict[str, dict[str, Any]] = {
    "word_emphasis": {
        "name": "逐词强调",
        "type": AnimationType.TEXT,
        "target": AnimationTarget.TEXT,
        "duration_sec": 0.6,
        "easing": EasingFunction.EASE_OUT,
        "keyframes": [
            Keyframe(time=0.0, properties={"char_progress": 0, "color_emphasis": 0}),
            Keyframe(time=0.5, properties={"char_progress": 1, "color_emphasis": 1}),
            Keyframe(time=1.0, properties={"char_progress": 1, "color_emphasis": 0.6}),
        ],
        "properties_meta": {
            "char_progress": PropertyDef(type="float", default=1, range=[0, 1], description="逐字出现进度"),
            "color_emphasis": PropertyDef(type="float", default=0, range=[0, 1], description="强调色强度"),
        },
    },
    "bounce_text": {
        "name": "弹跳入场",
        "type": AnimationType.ONSCREEN,
        "target": AnimationTarget.TEXT,
        "duration_sec": 0.6,
        "easing": EasingFunction.EASE_OUT_CUBIC,
        "keyframes": [
            Keyframe(time=0.0, properties={"opacity": 0, "translate_y": -100}),
            Keyframe(time=0.6, properties={"opacity": 1, "translate_y": 10}),
            Keyframe(time=0.8, properties={"opacity": 1, "translate_y": -5}),
            Keyframe(time=1.0, properties={"opacity": 1, "translate_y": 0}),
        ],
        "properties_meta": {
            "opacity": PropertyDef(type="float", default=1, range=[0, 1], description="不透明度"),
            "translate_y": PropertyDef(type="float", default=0, unit="px", description="纵向位移"),
        },
    },
    "elastic_scale": {
        "name": "弹性缩放",
        "type": AnimationType.ONSCREEN,
        "target": AnimationTarget.ANY,
        "duration_sec": 0.5,
        "easing": EasingFunction.EASE_OUT,
        "keyframes": [
            Keyframe(time=0.0, properties={"opacity": 0, "scale_x": 0.0, "scale_y": 0.0}),
            Keyframe(time=0.5, properties={"opacity": 1, "scale_x": 1.2, "scale_y": 1.2}),
            Keyframe(time=0.7, properties={"opacity": 1, "scale_x": 0.9, "scale_y": 0.9}),
            Keyframe(time=1.0, properties={"opacity": 1, "scale_x": 1.0, "scale_y": 1.0}),
        ],
        "properties_meta": {
            "opacity": PropertyDef(type="float", default=1, range=[0, 1], description="不透明度"),
            "scale_x": PropertyDef(type="float", default=1, description="横向缩放"),
            "scale_y": PropertyDef(type="float", default=1, description="纵向缩放"),
        },
    },
    "stroke_draw": {
        "name": "描边绘制",
        "type": AnimationType.TEXT,
        "target": AnimationTarget.TEXT,
        "duration_sec": 0.8,
        "easing": EasingFunction.EASE_OUT,
        "keyframes": [
            Keyframe(time=0.0, properties={"char_progress": 0}),
            Keyframe(time=1.0, properties={"char_progress": 1}),
        ],
        "properties_meta": {
            "char_progress": PropertyDef(type="float", default=1, range=[0, 1], description="逐字绘制进度"),
        },
    },
}

#: 已下线（审计 §3.11：keyframes 渲染器无对应属性，声明了也渲不出来）
RETIRED_ANIMATIONS = ("perspective_3d", "gradient_fill")


class KineticTypographyPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="kinetic_typography", name="Kinetic Typography", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="高级文字动画（keyframes 表达：逐词强调/弹跳/弹性缩放/描边绘制）",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：4 个 keyframes 动画 + structure 提示词。

        不再贡献目录扩展钩子——engine 从不读那个 context 键，
        留着只会让"插件已注册但动画不可用"看起来像 engine 的 bug。
        """
        prompt = (
            "## 高级文字动画（来自 kinetic_typography 插件）\n"
            "可用高级文字动画标记：\n"
            "- [文字动画]逐词强调 — 逐词放大变色强调\n"
            "- [文字动画]弹跳 — 弹跳入场效果\n"
            "- [文字动画]弹性缩放 — 弹性缩放动画\n"
            "- [文字动画]描边绘制 — 描边绘制入场\n\n"
            "使用 [文字动画]名称：要显示的文字 格式标记。"
        )
        items: list[Any] = [
            Contribution(
                ContributionKind.PROMPT, "kinetic_structure_prompt", prompt,
                meta={"agent": "structure", "prompt": prompt, "priority": 3},
            ),
        ]
        for anim_id, spec in KINETIC_ANIMATIONS.items():
            defn = AnimationDef(
                animation_id=anim_id,
                name=spec["name"],
                type=spec["type"],
                target=spec["target"],
                duration_sec=spec["duration_sec"],
                easing=spec["easing"],
                keyframes=spec["keyframes"],
                properties_meta=spec["properties_meta"],
                description=f"Kinetic typography: {spec['name']}",
                author="Clipwright Team",
                tags=["kinetic", "typography"],
            )
            items.append(Contribution(ContributionKind.ANIMATION, anim_id, defn))
        return items

    def shutdown(self) -> None:
        pass


__all__ = ["KineticTypographyPlugin", "KINETIC_ANIMATIONS", "RETIRED_ANIMATIONS"]
