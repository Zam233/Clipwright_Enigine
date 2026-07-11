"""鬼畜快剪插件 (kichiku_fastcut)。

剪辑特征：平均镜头 0.3-2s，闪白/Jump Cut，动画密度极高。
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.schema.persona import ParameterLayer


class KichikuFastcutPlugin(BaseCategoryPlugin):
    plugin_id = "kichiku_fastcut"
    display_name = "鬼畜快剪"
    description = "高速节奏、密集特效的鬼畜/二创视频"

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        density_map = {
            "low": {"base_shot_ms": 2000, "min_shot_ms": 500, "max_shot_ms": 4000},
            "medium": {"base_shot_ms": 1000, "min_shot_ms": 300, "max_shot_ms": 3000},
            "high": {"base_shot_ms": 500, "min_shot_ms": 100, "max_shot_ms": 2000},
        }
        shot_params = density_map.get(params.rhythm.cut_density_tier, density_map["high"])

        return {
            "shot_params": shot_params,
            "cut_profile": "rapid_fire",
            "transition_weights": {
                "hard_cut": 0.4,
                "glitch": 0.2,
                "pixel_dissolve": 0.2,
                "fade": 0.2,
            },
            "annotation_templates": [
                "hook",
                "quick_cut_sequence",
                "reaction_shot",
                "climax_silence",
            ],
        }

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return translated.get("shot_params", {})
