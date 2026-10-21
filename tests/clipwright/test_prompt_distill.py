"""提示词蒸馏单测（PRD §II.12 / F-05 后端部分）。"""

from __future__ import annotations

import pytest

from clipwright.services.prompt_distill import (
    MAX_AVOID_ITEMS,
    MAX_PROMPT_CHARS,
    distill_from_messages,
    distill_with_llm,
    normalize_draft,
)


def _msg(content: str, role: str = "user") -> dict:
    return {"role": role, "content": content}


class TestRuleDistill:
    def test_collects_user_requirements(self) -> None:
        draft = distill_from_messages([
            _msg("我要一支 MV"),
            _msg("风格是版画质感，三墨套印"),
        ])
        assert "MV" in draft.prompt and "版画质感" in draft.prompt
        assert draft.degraded is True
        assert draft.source_messages == 2

    def test_greetings_dropped(self) -> None:
        draft = distill_from_messages([_msg("你好"), _msg("hello"), _msg("要硬切为主")])
        assert "你好" not in draft.prompt
        assert draft.prompt == "要硬切为主"

    def test_negations_go_to_avoid(self) -> None:
        draft = distill_from_messages([_msg("不要花哨转场；要有数据图表")])
        assert draft.avoid == ["花哨转场"]
        assert "数据图表" in draft.prompt

    def test_avoid_dedup_and_limit(self) -> None:
        messages = [_msg("不要抖") for _ in range(3)]
        messages += [_msg(f"不要元素{i}") for i in range(10)]
        draft = distill_from_messages(messages)
        assert len(draft.avoid) <= MAX_AVOID_ITEMS
        assert len(set(draft.avoid)) == len(draft.avoid)

    def test_assistant_messages_ignored(self) -> None:
        draft = distill_from_messages([_msg("模型说的话", role="assistant"), _msg("要克制配色")])
        assert "模型说的话" not in draft.prompt
        assert draft.source_messages == 1

    def test_prompt_truncated(self) -> None:
        draft = distill_from_messages([_msg("长" * (MAX_PROMPT_CHARS + 500))])
        assert len(draft.prompt) <= MAX_PROMPT_CHARS

    def test_empty_input(self) -> None:
        draft = distill_from_messages([])
        assert draft.prompt == "" and draft.source_messages == 0


class TestNormalizeDraft:
    def test_accepts_llm_payload(self) -> None:
        draft = normalize_draft(
            {"prompt": " 版画风 MV ", "avoid": ["花哨转场", "花哨转场"], "rationale": "按需求"},
            source_messages=3,
        )
        assert draft.prompt == "版画风 MV"
        assert draft.avoid == ["花哨转场"]
        assert draft.degraded is False
        assert draft.source_messages == 3

    def test_avoid_as_string(self) -> None:
        assert normalize_draft({"prompt": "x", "avoid": "不要抖动"}).avoid == ["不要抖动"]

    def test_missing_prompt_is_degraded(self) -> None:
        assert normalize_draft({"prompt": ""}).degraded is True


class _FakeLLM:
    def __init__(self, payload=None, boom: bool = False) -> None:
        self.payload = payload
        self.boom = boom
        self.calls = 0

    async def structured_output(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.boom:
            raise RuntimeError("llm down")
        return self.payload


class TestLLMDistill:
    @pytest.mark.asyncio
    async def test_uses_llm_when_available(self) -> None:
        llm = _FakeLLM({"prompt": "版画 MV，硬切", "avoid": ["花哨转场"], "rationale": "依据对话"})
        draft = await distill_with_llm([_msg("要版画 MV")], llm=llm)
        assert draft.degraded is False
        assert draft.prompt == "版画 MV，硬切"
        assert llm.calls == 1

    @pytest.mark.asyncio
    async def test_falls_back_on_exception(self) -> None:
        draft = await distill_with_llm([_msg("要硬切")], llm=_FakeLLM(boom=True))
        assert draft.degraded is True
        assert "硬切" in draft.prompt

    @pytest.mark.asyncio
    async def test_falls_back_on_empty_payload(self) -> None:
        draft = await distill_with_llm([_msg("要硬切")], llm=_FakeLLM({"prompt": ""}))
        assert draft.degraded is True
        assert "硬切" in draft.prompt

    @pytest.mark.asyncio
    async def test_no_llm_uses_rules(self) -> None:
        draft = await distill_with_llm([_msg("要克制配色")], llm=None)
        assert draft.degraded is True
        assert "克制配色" in draft.prompt

    @pytest.mark.asyncio
    async def test_empty_transcript_skips_llm(self) -> None:
        llm = _FakeLLM({"prompt": "x"})
        draft = await distill_with_llm([], llm=llm)
        assert llm.calls == 0
        assert draft.prompt == ""
