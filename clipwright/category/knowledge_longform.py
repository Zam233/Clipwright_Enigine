"""知识区长片插件 (knowledge_longform)。

剪辑特征：平均镜头 5-15s，硬切为主，动画密度中。
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.schema.persona import ParameterLayer


class KnowledgeLongformPlugin(BaseCategoryPlugin):
    plugin_id = "knowledge_longform"
    display_name = "知识区长片"
    description = "高密度信息输出、强逻辑链条的知识类视频"

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        rhythm = params.rhythm

        # 将 Persona 的 cut_density_tier 翻译为具体的镜头时长
        density_map = {
            "low": {"base_shot_ms": 12000, "min_shot_ms": 3000, "max_shot_ms": 25000},
            "medium": {"base_shot_ms": 7000, "min_shot_ms": 2000, "max_shot_ms": 15000},
            "high": {"base_shot_ms": 4000, "min_shot_ms": 800, "max_shot_ms": 10000},
        }
        shot_params = density_map.get(rhythm.cut_density_tier, density_map["medium"])
        # Persona 显式 base_shot_duration_ms 优先于密度档位推导
        if getattr(rhythm, "base_shot_duration_ms", None):
            shot_params = {**shot_params,
                           "base_shot_ms": int(rhythm.base_shot_duration_ms)}

        return {
            "shot_params": shot_params,
            "cut_profile": rhythm.cut_profile,
            "transition_weights": {
                "hard_cut": 0.8,
                "dissolve": 0.1,
                "fade": 0.1,
            },
            "annotation_templates": [
                "hook",
                "theory_acceleration",
                "real_world_return",
                "climax_silence",
            ],
        }

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return translated.get("shot_params", {})

    def get_structure_template(self) -> str:
        """轮 84：知识讲解类场景骨架——钩子→论点→论证→实例→总结。"""
        return (
            "钩子引入（约10%）：抛出问题/反常识观点/悬念，口播为主、不配图表\n"
            "核心论点（约25%）：提出核心观点并拆解，配逻辑关系图/概念图辅助理解\n"
            "论据论证（约30%）：数据、原理、因果链条逐层展开，数据处配图表可视化\n"
            "现实实例（约20%）：真实案例/历史事件/应用场景佐证论点\n"
            "总结升华（约15%）：回顾要点、给出判断或思考，收束全片"
        )


    def get_annotation_templates(self) -> list[str]:
        """轮 87：节奏/标注模板（原 translate_persona 死键，现在结构 prompt 消费）。"""
        return ["hook", "theory_acceleration", "real_world_return", "climax_silence"]
    def get_mg_style_guidance(self) -> str:
        return (
            "知识区长片的 MG 动画风格：优雅、学术、有质感。"
            "偏好数据可视化（图表/时间线/流程图）、逻辑关系图；"
            "配色克制沉稳，动效节奏舒缓（0.3-0.6s 入场），"
            "避免过于花哨的霓虹/故障效果，注重信息的清晰传达。"
        )
