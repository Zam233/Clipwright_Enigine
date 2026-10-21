"""歌词 MV / PV 类型插件（lyric_mv）。

这是**第一个按新契约（10 槽位 + 参数规则 + 代理指标）写全的内置类型**：
此前四个内置插件都只走旧钩子，`load_spec()` 对它们只合成出 structure/animation
两个槽位、零参数规则、零指标——Part I 的机制在运行时等于空转。本插件把"类型"
写成机器可读的规范，让 V35（成片被判 lyric_mv）第一次有东西可判。

类型特征（MV/PV 的本质，不是参数）：

- 画面服务歌词：一句词一镜，切点落在拍子上，文字是主角而不是注释；
- 版画/印刷质感优先于实拍叙事，不追求"讲清楚一件事"；
- 反模式：把 MV 剪成口播知识视频、加花哨弹跳转场、堆说明性图表。
"""

from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.schema.category_spec import (
    ParamRule,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
    TypeDeclares,
)
from clipwright.schema.persona import ParameterLayer


class LyricMvPlugin(BaseCategoryPlugin):
    plugin_id = "lyric_mv"
    display_name = "歌词 MV / PV"
    description = "歌词驱动的音乐影像：一句词一镜，切点落拍，文字与版画质感为主"

    # ── 旧接口（保持兼容：参数翻译）────────────────────────────────────

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        tier = getattr(params.rhythm, "cut_density_tier", "medium")
        density_map = {
            # V35 自洽性：本类型声明 avg_shot_ms ≤3200ms（strict），节奏参数就必须有
            # 能力兑现它——上限超过 3200 的镜头会让同一性指标永远判不过。原先
            # low/medium 上限是 8000/6000，切分策略（shot_cap_policy=split）无从下手。
            "low": {"base_shot_ms": 3000, "min_shot_ms": 900, "max_shot_ms": 3200},
            "medium": {"base_shot_ms": 2400, "min_shot_ms": 600, "max_shot_ms": 3000},
            "high": {"base_shot_ms": 1600, "min_shot_ms": 400, "max_shot_ms": 2000},
        }
        shot_params = density_map.get(tier, density_map["medium"])
        if getattr(params.rhythm, "base_shot_duration_ms", None):
            shot_params = {**shot_params, "base_shot_ms": int(params.rhythm.base_shot_duration_ms)}
        return {
            "shot_params": shot_params,
            "cut_profile": "beat_driven",
            "transition_weights": {"hard_cut": 0.7, "dissolve": 0.2, "zoom_in": 0.1},
            "cut_on_beat": True,
            "annotation_templates": ["hook", "keyword", "climax_silence"],
        }

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return translated.get("shot_params", {})

    def get_pacing(self) -> dict[str, Any]:
        return {"cut_density": "high", "cut_on_beat": True}

    def get_structure_template(self) -> str:
        """MV 结构不是"开场-过程-收尾"，而是"动机-展开-变奏-收束"。"""
        return (
            "动机（约15%）：一句最有画面感的词先出现，建立质感基调\n"
            "展开（约40%）：主歌段落，一镜一句，切点落拍\n"
            "变奏（约30%）：副歌/情绪转折，画面更密、版画层次更重\n"
            "收束（约15%）：最后一句词留白，让音乐收尾"
        )

    def get_mg_style_guidance(self) -> str:
        return (
            "动效应像印刷：压印、套色错位、墨迹渗开、颗粒上浮；"
            "不做弹跳、不做花哨旋转；文字逐词落位而不是整句飞入。"
        )

    def recommended_extra_params(self) -> dict[str, Any]:
        return {
            "cut_on_beat": True,
            "mv_placement": True,
            "subtitle_enabled": True,
            "orientation": "landscape",
        }

    # ── 新契约：10 槽位 ───────────────────────────────────────────────

    def get_prompt_bundle(self) -> PromptBundle:
        """身份层说"这是什么片子"，阶段层说"这一段该怎么干"。"""
        return PromptBundle(slots={
            "genre": PromptSlot(
                text=(
                    "整片是一支歌词 MV：画面为歌词服务，一句词对应一个镜头，"
                    "切点落在音乐拍子上；文字与版画/印刷质感是主角，"
                    "不追求把一件事讲清楚，而追求情绪与质感的推进。"
                ),
                priority=90,
            ),
            "anti_patterns": PromptSlot(
                text=(
                    "禁止把 MV 剪成口播知识视频（无旁白讲解、无论证段落）；"
                    "禁止花哨弹跳/霓虹闪烁转场；禁止堆说明性图表；"
                    "禁止让字幕变成评论而不是歌词。"
                ),
                priority=85,
            ),
            "lexicon": PromptSlot(
                text=(
                    "可用意象：油墨、套印、错版、颗粒、刻痕、留白、印张。"
                    "避免词：本片将介绍、首先、其次、综上、数据显示。"
                ),
                priority=60,
            ),
            "structure": PromptSlot(
                text=(
                    "按动机-展开-变奏-收束拆段落，每段一句主导歌词；"
                    "段落长度跟随音乐，不按讲解视频的节奏分配。"
                ),
                priority=80,
            ),
            "material": PromptSlot(
                text=(
                    "优先手作质感素材：纸张纹理、油墨痕迹、刻版特写、印刷机运转；"
                    "实拍人物只在需要呈现「演唱者」时使用，且不与版画质感冲突。"
                ),
                priority=70,
            ),
            "edit": PromptSlot(
                text=(
                    "切点必须落在拍子上（cut_on_beat 生效）；"
                    "镜头时长服从 min_shot_ms 与 max_shot_ms 的区间，"
                    "宁可同素材复现也不拉长单镜。"
                ),
                priority=85,
            ),
            "animation": PromptSlot(
                text=(
                    "动画只做两件事：让文字逐词落位、让版画层次逐层印上；"
                    "严禁弹跳、旋转、彩虹渐变。"
                ),
                priority=80,
            ),
            "audio": PromptSlot(
                text=(
                    "BGM 全曲铺底，不做情绪大起大落的音量包络；"
                    "人声与音乐不抢——有人声时压低 BGM 但不做停顿切碎。"
                ),
                priority=70,
            ),
            "subtitle": PromptSlot(
                text=(
                    "字幕即歌词：逐词或逐句高亮，字体克制，"
                    "关键词加重但不加描边发光；位置避开画面主体。"
                ),
                priority=75,
            ),
            "quality": PromptSlot(
                text=(
                    "质检点：切点是否在拍子上、字幕是否只是歌词、"
                    "画面是否有印刷质感、有没有口播式解释文案。"
                ),
                priority=70,
            ),
        })

    # ── 新契约：参数规则（只允许剂量类键）──────────────────────────────

    def get_param_rules(self) -> list[ParamRule]:
        return [
            ParamRule(
                key="base_shot_ms", mode="ceiling", value=6000, enforce="soft",
                reason="MV 单镜不宜过长，长镜会让歌词错位",
            ),
            ParamRule(
                key="min_shot_ms", mode="floor", value=300, enforce="soft",
                reason="低于人类可辨识下限的镜头会像闪烁，触发闪光风险",
            ),
            ParamRule(
                key="cut_on_beat", mode="require", enforce="strict",
                reason="MV 的切点落拍是类型同一性的一部分，不是可选项",
            ),
            ParamRule(
                key="transition_weights.hard_cut", mode="floor", value=0.5, enforce="soft",
                reason="MV 以硬切为主，柔和转场只能点缀",
            ),
        ]

    # ── 新契约：代理指标（可机器判的"像不像 MV"）────────────────────────

    def get_signature_traits(self) -> list[SignatureTrait]:
        return [
            SignatureTrait(
                name="avg_shot_ms", mode="require_le", value=3200, enforce="strict",
                reason="MV 平均镜头短于普通叙事视频——不达标就不该被判成 MV",
            ),
            SignatureTrait(
                name="text_overlay_ratio", mode="require_ge", value=0.4, enforce="strict",
                reason="文字（歌词）必须是画面主体之一，不是零星注释",
            ),
        ]

    # ── 新契约：类型声明（取代 Agent 里的硬编码集合）────────────────────

    def get_declares(self) -> TypeDeclares:
        return TypeDeclares(
            required_animation_markers=["lyric_karaoke", "plate_print"],
            semantic_qa_default=None,
            search_hint="版画 油墨 套印 纸张纹理 刻版 印刷机 颗粒质感",
            plan_schema={
                "required": ["sections", "beats", "material_strategy"],
                "optional": ["screen_kinds", "risks"],
                "custom_allowed": False,
            },
            neighbors=["plate_collage", "pure_lyric_graphic"],
            requires=["beat_grid", "lyrics"],
        )


__all__ = ["LyricMvPlugin"]
