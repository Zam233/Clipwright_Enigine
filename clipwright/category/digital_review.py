"""数码评测插件 (digital_review)。

剪辑特征：平均镜头 3-8s，缓入缓出，动画密度中。
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.schema.persona import ParameterLayer


class DigitalReviewPlugin(BaseCategoryPlugin):
    plugin_id = "digital_review"
    display_name = "数码评测"
    description = "产品评测、数码体验类视频"

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        density_map = {
            "low": {"base_shot_ms": 8000, "min_shot_ms": 2000, "max_shot_ms": 15000},
            "medium": {"base_shot_ms": 5000, "min_shot_ms": 1500, "max_shot_ms": 10000},
            "high": {"base_shot_ms": 3000, "min_shot_ms": 800, "max_shot_ms": 8000},
        }
        shot_params = density_map.get(params.rhythm.cut_density_tier, density_map["medium"])
        # Persona 显式 base_shot_duration_ms 优先于密度档位推导
        if getattr(params.rhythm, "base_shot_duration_ms", None):
            shot_params = {**shot_params,
                           "base_shot_ms": int(params.rhythm.base_shot_duration_ms)}

        return {
            "shot_params": shot_params,
            "cut_profile": "smooth_flow",
            "transition_weights": {
                "hard_cut": 0.5,
                "dissolve": 0.3,
                "fade": 0.2,
            },
            "annotation_templates": [
                "hook",
                "product_broll",
                "theory_acceleration",
            ],
        }

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return translated.get("shot_params", {})

    def get_structure_template(self) -> str:
        """轮 84（数码评测专项）：评测类场景骨架——开箱→参数→实测→对比→结论。"""
        return (
            "开箱与外观（约15%）：第一印象、包装配件、外观细节与做工特写，画面以产品实拍/特写为主\n"
            "核心参数（约20%）：关键规格、价格定位，必须配参数表格或对比数据图表\n"
            "实测体验（约40%）：性能/续航/屏幕/影像等核心项目逐项实测，给出实测数据并配对比图表\n"
            "横向对比（约15%）：与同价位竞品的关键项对比，用对比图表呈现差异\n"
            "总结建议（约10%）：优缺点回顾、适合人群、一句话购买建议"
        )


    def get_annotation_templates(self) -> list[str]:
        """轮 87：节奏/标注模板（原 translate_persona 死键，现在结构 prompt 消费）。"""
        return ["hook", "product_broll", "theory_acceleration"]
    def get_mg_style_guidance(self) -> str:
        return (
            "数码评测的 MG 动画风格：科技感、精致、冷色调。"
            "偏好产品数据对比（参数表格/跑分柱状图/温度曲线）、"
            "规格参数高亮、性能排名；动效干脆利落（0.2-0.4s），"
            "常用描边线框、发光光效、渐变质感，背景多用深色。"
        )
