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
        # Persona 显式 base_shot_duration_ms 优先于密度档位推导
        if getattr(params.rhythm, "base_shot_duration_ms", None):
            shot_params = {**shot_params,
                           "base_shot_ms": int(params.rhythm.base_shot_duration_ms)}

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

    def get_structure_template(self) -> str:
        """轮 84：Vlog 场景骨架——开场→过程→高光→收尾。"""
        return (
            "开场引入（约10%）：问候+本期主题一句话预告，出镜口播为主\n"
            "过程记录（约40%）：按时间线记录本期事件，保持自然节奏与生活感\n"
            "高光时刻（约30%）：本期最有趣的2-3个片段展开，配情绪字幕\n"
            "感悟收尾（约20%）：当天感受/下期预告，轻松收束"
        )


    def recommended_extra_params(self) -> dict[str, Any]:
        """Vlog 推荐：调色 + 自动出片；视觉 LLM 对生活素材收益低不默认开。"""
        return {"enable_color_match": True, "auto_render": True}

    def get_annotation_templates(self) -> list[str]:
        """轮 87：节奏/标注模板（原 translate_persona 死键，现在结构 prompt 消费）。"""
        return ["hook", "reaction_shot", "climax_silence"]
    def get_mg_style_guidance(self) -> str:
        return (
            "Vlog 日常的 MG 动画风格：温暖、轻快、生活化。"
            "偏好简单的手绘感标注、地点/时间标签、暖色圆角图形；"
            "动效柔和自然（0.3-0.5s），避免工业感/冷硬科技风，"
            "文字动画多于数据图表。"
        )
