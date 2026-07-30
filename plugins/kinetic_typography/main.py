"""动态文字排版插件 — 高级 Kinetic Typography 动画。

新增动画类型（通过 ANIMATION_CATALOG_EXTEND hook 注册）：
  - word_emphasis: 逐词放大强调
  - bounce_text: 弹跳入场
  - elastic_scale: 弹性缩放
  - perspective_3d: 3D 透视旋转
  - gradient_fill: 渐变填充
  - stroke_draw: 描边绘制动画

渲染通过 Hyperframes HTML/CSS 实现。
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.hooks import HookRegistry, HookPoint
from clipwright.animation.registry import AnimationRegistry
from clipwright.schema.animation import AnimationDef, AnimationType
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

KINETIC_ANIMATIONS: dict[str, dict[str, Any]] = {
    "word_emphasis": {
        "name": "逐词强调",
        "type": "onscreen",
        "css": """
@keyframes wordPop { 0%{transform:scale(1)} 50%{transform:scale(1.4);color:#FFD700} 100%{transform:scale(1)} }
.word-emphasis span { display:inline-block; animation: wordPop 0.4s ease; }
""",
    },
    "bounce_text": {
        "name": "弹跳入场",
        "type": "onscreen",
        "css": """
@keyframes bounceIn { 0%{transform:translateY(-100px);opacity:0} 60%{transform:translateY(10px)} 80%{transform:translateY(-5px)} 100%{transform:translateY(0);opacity:1} }
.bounce-text { animation: bounceIn 0.6s cubic-bezier(0.68,-0.55,0.265,1.55); }
""",
    },
    "elastic_scale": {
        "name": "弹性缩放",
        "type": "onscreen",
        "css": """
@keyframes elasticScale { 0%{transform:scale(0)} 50%{transform:scale(1.2)} 70%{transform:scale(0.9)} 100%{transform:scale(1)} }
.elastic-scale { animation: elasticScale 0.5s ease-out; }
""",
    },
    "perspective_3d": {
        "name": "3D 透视",
        "type": "onscreen",
        "css": """
@keyframes perspective3d { 0%{transform:perspective(500px) rotateY(90deg);opacity:0} 100%{transform:perspective(500px) rotateY(0);opacity:1} }
.perspective-3d { animation: perspective3d 0.6s ease-out; }
""",
    },
    "gradient_fill": {
        "name": "渐变填充",
        "type": "onscreen",
        "css": """
@keyframes gradientShift { 0%{background-position:0% 50%} 100%{background-position:200% 50%} }
.gradient-fill { background: linear-gradient(90deg, #FF6B6B, #4ECDC4, #45B7D1, #FF6B6B); background-size:200%; -webkit-background-clip:text; -webkit-text-fill-color:transparent; animation: gradientShift 2s linear infinite; }
""",
    },
    "stroke_draw": {
        "name": "描边绘制",
        "type": "onscreen",
        "css": """
@keyframes strokeDraw { 0%{clip-path:inset(0 100% 0 0)} 100%{clip-path:inset(0 0 0 0)} }
.stroke-draw { -webkit-text-stroke: 2px #fff; color: transparent; animation: strokeDraw 0.8s ease-out forwards; }
""",
    },
}


def _extend_catalog(context: dict[str, Any]) -> dict[str, Any]:
    """ANIMATION_CATALOG_EXTEND hook: 注册动态文字动画到目录。"""
    catalog = context.get("catalog", {})
    for anim_id, anim_def in KINETIC_ANIMATIONS.items():
        catalog[anim_id] = anim_def
    context["catalog"] = catalog
    return context


class KineticTypographyPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="kinetic_typography", name="Kinetic Typography", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Advanced kinetic typography animations via CSS/Hyperframes",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        HookRegistry.register(HookPoint.ANIMATION_CATALOG_EXTEND, _extend_catalog, plugin_id=self.manifest.id)
        for anim_id, anim_def in KINETIC_ANIMATIONS.items():
            defn = AnimationDef(
                animation_id=anim_id, name=anim_def["name"],
                type=AnimationType.ONSCREEN, description=f"Kinetic typography: {anim_def['name']}",
                author="Clipwright Team", tags=["kinetic", "typography"],
            )
            AnimationRegistry.register(defn, plugin_id=self.manifest.id)
        logger.info("[KineticTypography] %d 种动态文字动画已注册", len(KINETIC_ANIMATIONS))

    def shutdown(self) -> None:
        pass


__all__ = ["KineticTypographyPlugin", "KINETIC_ANIMATIONS"]
