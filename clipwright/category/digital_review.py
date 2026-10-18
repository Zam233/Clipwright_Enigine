"""数码评测插件 (digital_review)。

剪辑特征：平均镜头 3-8s，缓入缓出，动画密度中。
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


    def recommended_extra_params(self) -> dict[str, Any]:
        """数码评测推荐：开视觉 LLM + 调色 + 自动出片（色准/参数图依赖视觉）。"""
        return {"enable_visual_llm": True, "enable_color_match": True, "auto_render": True}

    def get_annotation_templates(self) -> list[str]:
        """轮 87：节奏/标注模板（原 translate_persona 死键，现在结构 prompt 消费）。"""
        return ["hook", "product_broll", "theory_acceleration"]

    # ── 新契约（Part I）──────────────────────────────────────────────

    def get_prompt_bundle(self) -> PromptBundle:
        return with_slots(
            super().get_prompt_bundle(),
            genre=PromptSlot(
                text=(
                    "整片是一支有实测依据的数码评测：结论先行、证据跟上，"
                    "每个判断都要落到可复核的测试结果上。"
                ),
                priority=90,
            ),
            anti_patterns=PromptSlot(
                text=(
                    "禁止只有主观感受没有测试；禁止堆参数却不解释对体验的影响；"
                    "禁止照抄官方宣传话术。"
                ),
                priority=85,
            ),
            lexicon=PromptSlot(
                text=(
                    "可用：实测、跑分、续航、温控、色准、噪声、对比组。"
                    "避免：遥遥领先、吊打、碾压、信仰加持。"
                ),
                priority=60,
            ),
            structure=PromptSlot(
                text=(
                    "场景骨架：结论先行 → 实测项目逐项展开 → 横向对比 → 适合谁买。"
                    "每个实测结论后面紧跟对比画面或数据。"
                ),
                priority=80,
            ),
            edit=PromptSlot(
                text=(
                    "镜头中速推进，对比处加快；"
                    "数据画面要留够阅读时间再切走。"
                ),
                priority=80,
            ),
            animation=PromptSlot(
                text=(
                    "动画承担数据表达：参数表、柱状对比、温度与续航曲线；"
                    "入场干脆、数值可读，冷色调科技感，不用弹跳与彩虹渐变。"
                ),
                priority=70,
            ),
            quality=PromptSlot(
                text=(
                    "质检点：数据是否有测试来源、结论是否与数据矛盾、"
                    "有没有把个例当通例。"
                ),
                priority=75,
            ),
        )

    def get_param_rules(self) -> list[ParamRule]:
        return [
            ParamRule(
                key="base_shot_ms", mode="ceiling", value=8000, enforce="soft",
                reason="评测片节奏要稳但不应拖成讲解长镜",
            ),
            ParamRule(
                key="transition_weights.hard_cut", mode="floor", value=0.4, enforce="soft",
                reason="数据与实测之间用硬切保持干脆",
            ),
        ]

    def get_signature_traits(self) -> list[SignatureTrait]:
        return [
            SignatureTrait(
                name="avg_shot_ms", mode="require_le", value=7000, enforce="soft",
                reason="评测片比知识长片更紧凑",
            ),
            SignatureTrait(
                name="argument_segment_count", mode="require_ge", value=4, enforce="soft",
                reason="评测至少要有结论/实测/对比/建议四段",
            ),
        ]

    def get_mg_style_guidance(self) -> str:
        return (
            "数码评测的 MG 动画风格：科技感、精致、冷色调。"
            "偏好产品数据对比（参数表格/跑分柱状图/温度曲线）、"
            "规格参数高亮、性能排名；动效干脆利落（0.2-0.4s），"
            "常用描边线框、发光光效、渐变质感，背景多用深色。"
        )
