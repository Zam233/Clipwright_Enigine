"""Vlog 日常插件 (vlog_daily)。

剪辑特征：平均镜头 3-10s，混合转场，动画密度低。
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.schema.persona import ParameterLayer


class VlogDailyPlugin(BaseCategoryPlugin):
    plugin_id = "vlog_daily"
    display_name = "Vlog 日常"
    description = "个人日常记录、生活分享类视频"

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        density_map = {
            "low": {"base_shot_ms": 10000, "min_shot_ms": 3000, "max_shot_ms": 20000},
            "medium": {"base_shot_ms": 6000, "min_shot_ms": 2000, "max_shot_ms": 12000},
            "high": {"base_shot_ms": 3000, "min_shot_ms": 1000, "max_shot_ms": 8000},
        }
        shot_params = density_map.get(params.rhythm.cut_density_tier, density_map["low"])

        return {
            "shot_params": shot_params,
            "cut_profile": "natural_flow",
            "transition_weights": {
                "hard_cut": 0.4,
                "dissolve": 0.3,
                "fade": 0.3,
            },
            "annotation_templates": [
                "hook",
                "reaction_shot",
                "climax_silence",
            ],
        }

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return translated.get("shot_params", {})

    def get_mg_style_guidance(self) -> str:
        return (
            "Vlog 日常的 MG 动画风格：温暖、轻快、生活化。"
            "偏好简单的手绘感标注、地点/时间标签、暖色圆角图形；"
            "动效柔和自然（0.3-0.5s），避免工业感/冷硬科技风，"
            "文字动画多于数据图表。"
        )
