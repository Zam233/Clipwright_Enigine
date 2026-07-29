"""新闻评论视频类型插件。

特点：下三分之一人名条、来源引用叠加、分屏对比、正式节奏、证据高亮动画。
"""
from __future__ import annotations
from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind

class NewsCategoryPlugin(BaseCategoryPlugin):
    manifest = PluginManifest(id="news_category", name="News Commentary Type", version="1.0.0",
        kind=PluginKind.CATEGORY, description="News/commentary video editing strategy", author="Clipwright Team")

    def get_shot_params(self) -> dict:
        return {"min_shot_sec": 4.0, "max_shot_sec": 12.0, "transition_type": "fade", "transition_duration_sec": 0.5, "cut_on_beat": False}

    def get_structure_template(self) -> list[dict]:
        return [{"role": "headline", "weight": 1.0, "desc": "新闻标题/导语"}, {"role": "report", "weight": 3.0, "desc": "事件报道"},
                {"role": "analysis", "weight": 2.0, "desc": "分析评论"}, {"role": "evidence", "weight": 1.5, "desc": "证据/数据展示"},
                {"role": "conclusion", "weight": 1.0, "desc": "总结观点"}]

    def get_annotation_templates(self) -> list[dict]:
        return [{"type": "lower_third", "position": "bottom-left", "style": "name_bar"},
                {"type": "source_citation", "position": "bottom-right", "style": "small_italic"},
                {"type": "split_screen", "position": "center", "style": "dual_pane"},
                {"type": "data_highlight", "position": "center", "style": "emphasis_box"}]

    def get_pacing(self) -> dict:
        return {"cuts_per_minute": 8, "text_density": "high", "animation_style": "formal", "bgm_volume": 0.1}

    def initialize(self) -> None:
        CategoryRegistry.register(self, plugin_id=self.manifest.id)
        print("[NewsCategory] 新闻评论类型已注册")

    def shutdown(self) -> None: pass

__all__ = ["NewsCategoryPlugin"]
