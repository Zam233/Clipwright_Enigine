"""用户创作提示词单测（PRD §II.12 R2 / F-04 落点）。"""

from __future__ import annotations

import pytest

from clipwright.services.creative_prompt import (
    MAX_AVOID_ITEMS,
    MAX_PROMPT_CHARS,
    creative_prompt_block,
    has_creative_prompt,
    merge_into_prompts,
    resolve_creative_prompt,
)


class TestResolve:
    def test_reads_prompt_and_avoid(self) -> None:
        prompt, avoid = resolve_creative_prompt(
            {"creative_prompt": "版画质感，硬切", "creative_avoid": ["花哨转场", "抖动"]}
        )
        assert prompt == "版画质感，硬切"
        assert avoid == ["花哨转场", "抖动"]

    def test_missing_is_empty(self) -> None:
        assert resolve_creative_prompt({}) == ("", [])
        assert resolve_creative_prompt(None) == ("", [])

    def test_avoid_as_string(self) -> None:
        assert resolve_creative_prompt({"creative_avoid": "不要抖动"})[1] == ["不要抖动"]

    def test_avoid_dedup_and_limit(self) -> None:
        raw = ["抖动", "抖动"] + [f"元素{i}" for i in range(10)]
        assert len(resolve_creative_prompt({"creative_avoid": raw})[1]) <= MAX_AVOID_ITEMS

    def test_prompt_truncated(self) -> None:
        prompt, _ = resolve_creative_prompt({"creative_prompt": "长" * (MAX_PROMPT_CHARS + 200)})
        assert len(prompt) <= MAX_PROMPT_CHARS

    def test_whitespace_collapsed(self) -> None:
        prompt, _ = resolve_creative_prompt({"creative_prompt": "  版画\n\n质感  "})
        assert prompt == "版画 质感"

    def test_has_creative_prompt(self) -> None:
        assert has_creative_prompt({"creative_prompt": "x"})
        assert not has_creative_prompt({"creative_prompt": "   "})


class TestBlock:
    def test_empty_returns_empty_string(self) -> None:
        assert creative_prompt_block({}) == ""

    def test_block_contains_priority_notice(self) -> None:
        block = creative_prompt_block({"creative_prompt": "版画质感"})
        assert "用户创作提示词" in block
        assert "优先级高于类型默认风格" in block
        assert "版画质感" in block

    def test_block_lists_avoid(self) -> None:
        block = creative_prompt_block({"creative_avoid": ["花哨转场"]})
        assert "用户明确不要的" in block
        assert "- 花哨转场" in block

    def test_block_mentions_platform_floor(self) -> None:
        block = creative_prompt_block({"creative_prompt": "x"})
        assert "平台硬底线" in block

    def test_block_deterministic(self) -> None:
        payload = {"creative_prompt": "a", "creative_avoid": ["b"]}
        assert creative_prompt_block(payload) == creative_prompt_block(payload)


class TestMerge:
    def test_merge_appends_block(self) -> None:
        merged = merge_into_prompts("类型规范文本", {"creative_prompt": "用户要求"})
        assert merged.startswith("类型规范文本")
        assert "用户创作提示词" in merged

    def test_merge_noop_without_prompt(self) -> None:
        assert merge_into_prompts("类型规范文本", {}) == "类型规范文本"

    def test_merge_empty_base_returns_block(self) -> None:
        merged = merge_into_prompts("   ", {"creative_prompt": "x"})
        assert merged.startswith("## 用户创作提示词")


class _Ctx:
    def __init__(self, extra: dict) -> None:
        self.extra_params = extra
        self.pipeline_id = "p" * 32
        self.persona_id = "demo"
        self.category_plugin_id = "lyric_mv"
        self.topic = "示范"


class TestAgentWiring:
    """F-04 落点：结构/需求 Agent 都会消费创作提示词。"""

    def test_structure_prompt_includes_creative_prompt(self) -> None:
        import inspect

        from clipwright.agents import structure_agent

        source = inspect.getsource(structure_agent.StructureAgent.execute)
        assert "creative_prompt_block" in source

    def test_requirements_prompt_includes_creative_prompt(self) -> None:
        import inspect

        from clipwright.agents import requirements_agent

        source = inspect.getsource(requirements_agent.RequirementsAgent._generate_brief)
        assert "resolve_creative_prompt" in source
        assert "用户创作提示词" in source
