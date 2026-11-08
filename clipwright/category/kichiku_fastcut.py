"""鬼畜快剪插件 (kichiku_fastcut)。

剪辑特征：平均镜头 0.3-2s，闪白/Jump Cut，动画密度极高。
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin, with_slots
from clipwright.schema.category_spec import (
    ParamRule,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
)
from clipwright.schema.persona import ParameterLayer


class KichikuFastcutPlugin(BaseCategoryPlugin):
    plugin_id = "kichiku_fastcut"
    display_name = "鬼畜快剪"
    description = "高速节奏、密集特效的鬼畜/二创视频"

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        density_map = {
            # 本类型声明 avg_shot_ms ≤ 1800ms（同一性指标），low 档基准必须落在
            # 指标区间内，否则该档永远判不过（V2/V35 自洽性）。
            "low": {"base_shot_ms": 1800, "min_shot_ms": 500, "max_shot_ms": 4000},
            "medium": {"base_shot_ms": 1000, "min_shot_ms": 300, "max_shot_ms": 3000},
            "high": {"base_shot_ms": 500, "min_shot_ms": 100, "max_shot_ms": 2000},
        }
        shot_params = density_map.get(params.rhythm.cut_density_tier, density_map["high"])
        # Persona 显式 base_shot_duration_ms 优先于密度档位推导
        if getattr(params.rhythm, "base_shot_duration_ms", None):
            shot_params = {**shot_params,
                           "base_shot_ms": int(params.rhythm.base_shot_duration_ms)}

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

    def get_structure_template(self) -> str:
        """轮 84：鬼畜快剪场景骨架——高能开场→素材快切→循环递进→收尾定格。"""
        return (
            "高能开场（约10%）：最强冲击素材直接开炸，3 秒内抓住注意力\n"
            "素材快切（约45%）：核心素材按节拍高频快切，重复洗脑循环\n"
            "循环递进（约35%）：素材变奏/加速/多轨叠加，情绪逐层推高\n"
            "定格收尾（约10%）：情绪顶点戛然而止或彩蛋定格"
        )


    def recommended_extra_params(self) -> dict[str, Any]:
        """鬼畜快剪推荐：仅自动出片（高饱和快切对调色/视觉不敏感）。"""
        return {"auto_render": True}

    def get_annotation_templates(self) -> list[str]:
        """轮 87：节奏/标注模板（原 translate_persona 死键，现在结构 prompt 消费）。"""
        return ["hook", "quick_cut_sequence", "reaction_shot", "climax_silence"]

    # ── 新契约（Part I）──────────────────────────────────────────────

    def get_prompt_bundle(self) -> PromptBundle:
        return with_slots(
            super().get_prompt_bundle(),
            genre=PromptSlot(
                text=(
                    "整片是节奏驱动的鬼畜快剪：靠重复、错位与音画卡点制造笑点，"
                    "叙事让位于节奏，情绪靠剪辑本身推。"
                ),
                priority=90,
            ),
            anti_patterns=PromptSlot(
                text=(
                    "禁止长镜与缓慢运镜；禁止解说式口播段落；禁止总结陈词；"
                    "禁止为了讲清楚而牺牲节奏。"
                ),
                priority=85,
            ),
            lexicon=PromptSlot(
                text=(
                    "可用：卡点、复读、错位、倍速、倒放、跳切、谐音。"
                    "避免：首先、其次、综上、本期我们。"
                ),
                priority=60,
            ),
            structure=PromptSlot(
                text=(
                    "场景骨架：抛出素材 → 加速堆叠 → 变奏反转 → 收尾爆点。"
                    "每段只做一件事，段落之间直接跳切。"
                ),
                priority=80,
            ),
            edit=PromptSlot(
                text=(
                    "镜头以 base_shot_ms 为上限，能短则短；"
                    "同一素材反复重排，切点踩在拍子与重音上。"
                ),
                priority=85,
            ),
            animation=PromptSlot(
                text=(
                    "动效可以夸张但要受闪光闸约束：抖动、故障、缩放均可，"
                    "高频闪烁必须落在安全阈值内。"
                ),
                priority=75,
            ),
            quality=PromptSlot(
                text=(
                    "质检点：闪光是否超标、重复是否服务笑点、"
                    "有没有悄悄变成讲解视频。"
                ),
                priority=70,
            ),
        )

    def get_param_rules(self) -> list[ParamRule]:
        return [
            ParamRule(
                key="base_shot_ms", mode="ceiling", value=1500, enforce="strict",
                reason="鬼畜的节奏不允许长镜，超过就不是这个类型",
            ),
            ParamRule(
                key="transition_weights.hard_cut", mode="floor", value=0.7, enforce="soft",
                reason="快剪以硬切与跳切为主",
            ),
        ]

    def get_signature_traits(self) -> list[SignatureTrait]:
        return [
            SignatureTrait(
                name="avg_shot_ms", mode="require_le", value=1800, enforce="soft",
                reason="鬼畜的视频片段明显短于其他类型",
            ),
            SignatureTrait(
                name="repeat_shot_ratio", mode="require_ge", value=0.15, enforce="soft",
                reason="重复与重排是这个类型的手艺",
            ),
        ]

    def get_mg_style_guidance(self) -> str:
        return (
            "鬼畜快剪的 MG 动画风格：高能、夸张、冲击力强。"
            "偏好弹幕式大字、故障抖动、快速缩放、震动效果、像素风；"
            "动效极快（0.1-0.3s 内完成），高频闪烁，"
            "颜色高饱和（大红/亮黄/电紫），可以打破常规排版。"
        )
