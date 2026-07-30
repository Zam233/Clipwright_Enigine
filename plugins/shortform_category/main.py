"""短视频类型插件 — 抖音/TikTok/Shorts 竖屏短视频剪辑策略。

特点：
  - 9:16 竖屏 (1080x1920)
  - 1-3 秒超短镜头，快节奏
  - Hook 优先结构（前 1.5 秒抓眼球）
  - 文字密集（大字幕、关键词弹出）
  - 快速转场（硬切/闪白/缩放）
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.plugins.hooks import HookRegistry, HookPoint
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class ShortformCategoryPlugin(BaseCategoryPlugin):
    plugin_id = "shortform_category"
    manifest = PluginManifest(
        id="shortform_category", name="Short-form Video Type", version="1.0.0",
        kind=PluginKind.CATEGORY,
        description="Vertical short-form video for Douyin/TikTok/Shorts",
        author="Clipwright Team",
    )

    def translate_persona(self, params) -> dict:
        return {"style": "shortform", "cut_density": "very_high", "aspect": "9:16"}

    def get_shot_params(self, translated=None) -> dict:
        return {
            "min_shot_sec": 1.0,
            "max_shot_sec": 3.0,
            "transition_type": "cut",
            "transition_duration_sec": 0.1,
            "cut_on_beat": True,
        }

    def get_structure_template(self) -> list[dict]:
        return [
            {"role": "hook", "weight": 1.0, "desc": "前 1.5 秒抓眼球（悬念/冲突/亮点）"},
            {"role": "content", "weight": 4.0, "desc": "快节奏内容主体"},
            {"role": "cta", "weight": 0.5, "desc": "结尾引导（关注/点赞/评论）"},
        ]

    def get_annotation_templates(self) -> list[dict]:
        return [
            {"type": "big_subtitle", "position": "center-bottom", "style": "bold_outline", "font_size": 56},
            {"type": "keyword_popup", "position": "center", "style": "bounce", "font_size": 72},
            {"type": "emoji_reaction", "position": "top-right", "style": "pop"},
            {"type": "progress_bar", "position": "top", "style": "thin"},
        ]

    def get_pacing(self) -> dict:
        return {
            "cuts_per_minute": 30,
            "text_density": "very_high",
            "animation_style": "energetic",
            "bgm_volume": 0.4,
        }

    def get_dimensions(self) -> dict:
        return {"width": 1080, "height": 1920, "aspect": "9:16"}

    def initialize(self) -> None:
        CategoryRegistry.register(self, plugin_id=self.manifest.id)
        HookRegistry.register(HookPoint.PRE_RENDER, self._inject_vertical, plugin_id=self.manifest.id)
        logger.info("[ShortformCategory] 短视频类型已注册 (1080x1920 9:16)")

    @staticmethod
    def _inject_vertical(context: dict[str, Any]) -> dict[str, Any]:
        """PRE_RENDER hook: 强制竖屏尺寸。"""
        settings = context.get("settings", {})
        settings["width"] = 1080
        settings["height"] = 1920
        context["settings"] = settings
        return context

    def shutdown(self) -> None:
        pass


__all__ = ["ShortformCategoryPlugin"]
