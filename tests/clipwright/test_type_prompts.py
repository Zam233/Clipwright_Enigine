"""类型提示词注入单测（PRD F1/F4/V1/V15）。

覆盖三件事：
1. 固定序（原文 → 身份 → 阶段）与"无槽位零变化"；
2. 6 个有 LLM 的阶段各自真的吃到类型规范（用假插件注册进 registry）；
3. material 槽位（目前唯一没有 LLM 消费者的阶段）落到检索式上。
"""

from __future__ import annotations

import pytest

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import PromptBundle, PromptSlot
from clipwright.services import type_prompts

CATEGORY_ID = "probe_typeprompt"


class _ProbePlugin(BaseCategoryPlugin):
    """只声明提示词槽位的假类型（不写进产品目录，测试结束即注销）。"""

    plugin_id = CATEGORY_ID
    display_name = "探针·类型提示词"
    description = "V1/V15 单测用"

    def translate_persona(self, params):  # noqa: ANN001, ANN201 - 基类签名
        return {"shot_params": {"base_shot_ms": 5000}}

    def get_shot_params(self, translated):  # noqa: ANN001, ANN201
        return translated.get("shot_params", {})

    def get_prompt_bundle(self) -> PromptBundle:
        return PromptBundle(slots={
            "genre": PromptSlot(text="整片是一场版画演出", priority=90),
            "anti_patterns": PromptSlot(text="不要花哨转场", priority=85),
            "structure": PromptSlot(text="结构：开场即入戏", priority=80),
            "material": PromptSlot(text="选片：只用手作质感素材", priority=70),
            "edit": PromptSlot(text="剪辑：硬切为主", priority=80),
            "animation": PromptSlot(text="动画：三墨套印逐层印上", priority=80),
            "audio": PromptSlot(text="音频：留白多于配乐", priority=80),
            "subtitle": PromptSlot(text="字幕：宋体竖排，克制", priority=80),
            "quality": PromptSlot(text="质检：任何荧光色都算失败", priority=80),
        })


@pytest.fixture()
def probe_category():
    CategoryRegistry.register(_ProbePlugin())
    yield CATEGORY_ID
    CategoryRegistry.unregister(CATEGORY_ID)


class TestCompose:
    def test_identity_and_stage_in_fixed_order(self, probe_category) -> None:
        text = type_prompts.compose("BASE", probe_category, "edit")
        assert text.index("BASE") < text.index("视频类型身份") < text.index("本阶段类型规范（edit）")
        assert "整片是一场版画演出" in text
        assert "不要花哨转场" in text
        assert "剪辑：硬切为主" in text
        # 别的阶段不串味
        assert "动画：三墨套印逐层印上" not in text

    def test_deterministic(self, probe_category) -> None:
        first = type_prompts.compose("BASE", probe_category, "edit")
        second = type_prompts.compose("BASE", probe_category, "edit")
        assert first == second

    def test_unknown_category_is_noop(self) -> None:
        assert type_prompts.compose("BASE", "", "edit") == "BASE\n"
        assert type_prompts.compose("BASE", "no_such_type", "edit") == "BASE\n"

    def test_identity_only_stage(self, probe_category) -> None:
        text = type_prompts.compose("BASE", probe_category, "")
        assert "整片是一场版画演出" in text
        assert "本阶段类型规范" not in text

    def test_compose_many_keeps_order(self, probe_category) -> None:
        text = type_prompts.compose_many("BASE", probe_category, ("audio", "subtitle"))
        assert text.index("音频：留白多于配乐") < text.index("字幕：宋体竖排，克制")

    def test_stage_summary_lists_declared(self, probe_category) -> None:
        summary = type_prompts.stage_summary(probe_category)
        assert set(summary["identity"]) == {"genre", "anti_patterns"}
        assert {"edit", "animation", "audio", "subtitle", "quality", "structure", "material"} <= set(
            summary["stages"]
        )
        assert summary["total_chars"] > 0


class TestStructurePromptWiring:
    """结构阶段的注入抽成模块函数，避免"接线丢了但没测试"的静默退化。"""

    def test_compose_structure_prompt_injects_identity_and_stage(self, probe_category) -> None:
        from clipwright.agents.structure_agent import compose_structure_prompt

        text = compose_structure_prompt("BASE", probe_category)
        assert "结构：开场即入戏" in text
        assert "整片是一场版画演出" in text
        assert text.index("BASE") < text.index("视频类型身份")
        assert text.index("视频类型身份") < text.index("本阶段类型规范（structure）")

    def test_compose_structure_prompt_noop_without_slots(self) -> None:
        from clipwright.agents.structure_agent import compose_structure_prompt

        assert compose_structure_prompt("BASE", "no_such_type").strip() == "BASE"


class TestMaterialHint:
    def test_material_slot_becomes_search_hint(self, probe_category) -> None:
        from clipwright.agents.material_agent import _category_search_hint

        assert _category_search_hint(probe_category) == "选片：只用手作质感素材"

    def test_builtin_type_keeps_legacy_hint(self) -> None:
        from clipwright.agents.material_agent import _category_search_hint

        hint = _category_search_hint("vlog_daily")
        assert isinstance(hint, str)
