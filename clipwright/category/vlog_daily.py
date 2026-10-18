"""Vlog 日常插件 (vlog_daily)。

剪辑特征：平均镜头 3-10s，混合转场，动画密度低。
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

    # ── 新契约（Part I）──────────────────────────────────────────────

    def get_prompt_bundle(self) -> PromptBundle:
        return with_slots(
            super().get_prompt_bundle(),
            genre=PromptSlot(
                text=(
                    "整片是一支自然旁观的日常 Vlog：记录真实发生的时刻，"
                    "保留呼吸与停顿，不刻意设计每一个镜头。"
                ),
                priority=90,
            ),
            anti_patterns=PromptSlot(
                text=(
                    "禁止广告腔与过度设计；禁止机械式平均切镜；"
                    "禁止为了节奏把生活片段剪成卡点拼贴。"
                ),
                priority=85,
            ),
            lexicon=PromptSlot(
                text=(
                    "可用：今天、刚好、顺手、过了一会儿、回来的路上。"
                    "避免：首先、其次、综上、数据显示。"
                ),
                priority=60,
            ),
            structure=PromptSlot(
                text=(
                    "场景骨架：开场问候 → 过程记录 → 高光时刻 → 感悟收尾。"
                    "过程段允许闲聊与空镜，不必每段都推进信息。"
                ),
                priority=80,
            ),
            edit=PromptSlot(
                text=(
                    "跟随事件自然分段，长镜与空镜交替；"
                    "切点落在动作完成或话题转折处，不强求踩拍。"
                ),
                priority=80,
            ),
            animation=PromptSlot(
                text=(
                    "只用手绘感标注、地点与时间标签；"
                    "动效柔和，避免工业感与冷硬科技风。"
                ),
                priority=70,
            ),
            quality=PromptSlot(
                text=(
                    "质检点：是否像真实记录而不是摆拍、"
                    "字幕语气是否自然、有没有过度设计。"
                ),
                priority=70,
            ),
        )

    def get_param_rules(self) -> list[ParamRule]:
        return [
            ParamRule(
                key="base_shot_ms", mode="floor", value=3000, enforce="soft",
                reason="日常片段需要呼吸，切得太快会变成快剪",
            ),
            ParamRule(
                key="transition_weights.hard_cut", mode="floor", value=0.3, enforce="soft",
                reason="以硬切为主，过渡转场只用于时间跳跃",
            ),
        ]

    def get_signature_traits(self) -> list[SignatureTrait]:
        return [
            SignatureTrait(
                name="avg_shot_ms", mode="require_ge", value=2000, enforce="soft",
                reason="Vlog 的镜头普遍比娱乐向内容长",
            ),
            SignatureTrait(
                name="text_overlay_ratio", mode="require_le", value=0.6, enforce="soft",
                reason="文字是点缀，不该盖满全片",
            ),
        ]

    def get_mg_style_guidance(self) -> str:
        return (
            "Vlog 日常的 MG 动画风格：温暖、轻快、生活化。"
            "偏好简单的手绘感标注、地点/时间标签、暖色圆角图形；"
            "动效柔和自然（0.3-0.5s），避免工业感/冷硬科技风，"
            "文字动画多于数据图表。"
        )
