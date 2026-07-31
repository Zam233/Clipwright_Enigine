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

    def get_mg_style_guidance(self) -> str:
        return (
            "数码评测的 MG 动画风格：科技感、精致、冷色调。"
            "偏好产品数据对比（参数表格/跑分柱状图/温度曲线）、"
            "规格参数高亮、性能排名；动效干脆利落（0.2-0.4s），"
            "常用描边线框、发光光效、渐变质感，背景多用深色。"
        )
