"""教程视频类型插件 — 教育/教程内容的剪辑策略。

特点：
  - 较长镜头（5-15 秒），适合演示和讲解
  - 步骤化结构（intro → step1 → step2 → ... → summary）
  - 代码/文字高亮动画
  - 章节标记
  - 慢节奏剪辑，强调清晰度
"""

from __future__ import annotations

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind


class TutorialCategoryPlugin(BaseCategoryPlugin):
    plugin_id = "tutorial_category"
    manifest = PluginManifest(
        id="tutorial_category", name="Tutorial Video Type", version="1.0.0",
        kind=PluginKind.CATEGORY,
        description="Educational/tutorial video editing strategy",
        author="Clipwright Team",
    )

    def translate_persona(self, params) -> dict:
        return {"style": "tutorial", "cut_density": "low"}

    def get_shot_params(self, translated=None) -> dict:
        return {
            "min_shot_sec": 5.0,
            "max_shot_sec": 15.0,
            "transition_type": "fade",
            "transition_duration_sec": 0.8,
            "cut_on_beat": False,
        }

    def get_structure_template(self) -> list[dict]:
        return [
            {"role": "intro", "weight": 1.0, "desc": "课程/教程开头引入"},
            {"role": "step", "weight": 3.0, "desc": "分步讲解（可重复多次）"},
            {"role": "demo", "weight": 2.0, "desc": "实操演示"},
            {"role": "summary", "weight": 1.0, "desc": "总结回顾"},
        ]

    def get_annotation_templates(self) -> list[dict]:
        return [
            {"type": "chapter_marker", "position": "top-left", "style": "minimal"},
            {"type": "step_number", "position": "top-right", "style": "badge"},
            {"type": "code_highlight", "position": "bottom", "style": "monospace"},
            {"type": "key_point", "position": "center", "style": "emphasis"},
        ]

    def get_pacing(self) -> dict:
        return {
            "cuts_per_minute": 6,
            "text_density": "high",
            "animation_style": "clean",
            "bgm_volume": 0.15,
        }

    def get_mg_style_guidance(self) -> str:
        return (
            "教程/教学类视频的 MG 动画风格：清晰、简洁、教学化。"
            "偏好步骤序号、章节标记、代码高亮、操作示意箭头、流程框图；"
            "动效平缓（0.3-0.5s），配色清爽（蓝/绿/白），"
            "图形规范对齐，把信息讲清楚比炫技更重要。"
        )

    def initialize(self) -> None:
        CategoryRegistry.register(self, plugin_id=self.manifest.id)
        print(f"[TutorialCategory] 教程视频类型已注册")

    def shutdown(self) -> None:
        pass


__all__ = ["TutorialCategoryPlugin"]
