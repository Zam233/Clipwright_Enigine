"""阶段注入的接线单测：真调各 Agent 的 LLM 决策函数，断言 system prompt 里带类型规范。

与 `test_type_prompts.py`（纯函数）互补：那一层证明"拼得对"，这一层证明"真的拼了"。
LLM 一律被打桩——这里验的是提示词内容，不验模型能力。
"""

from __future__ import annotations

import pytest

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import PromptBundle, PromptSlot

CATEGORY_ID = "probe_stage_wiring"


class _ProbePlugin(BaseCategoryPlugin):
    plugin_id = CATEGORY_ID
    display_name = "探针·阶段接线"
    description = "V1 接线单测用"

    def translate_persona(self, params):  # noqa: ANN001, ANN201
        return {"shot_params": {"base_shot_ms": 5000}}

    def get_shot_params(self, translated):  # noqa: ANN001, ANN201
        return translated.get("shot_params", {})

    def get_prompt_bundle(self) -> PromptBundle:
        return PromptBundle(slots={
            "genre": PromptSlot(text="身份印记：整片是一台印钞机", priority=90),
            "anti_patterns": PromptSlot(text="反模式印记：禁止弹跳动画", priority=85),
            "edit": PromptSlot(text="剪辑印记：每 2.4 秒一刀", priority=80),
            "audio": PromptSlot(text="音频印记：鼓点卡在呼吸上", priority=80),
            "subtitle": PromptSlot(text="字幕印记：关键词加粗描红", priority=80),
            "quality": PromptSlot(text="质检印记：检查是否有荧光绿", priority=80),
            "animation": PromptSlot(text="动画印记：印痕逐层叠上", priority=80),
        })


@pytest.fixture()
def probe_category():
    CategoryRegistry.register(_ProbePlugin())
    yield CATEGORY_ID
    CategoryRegistry.unregister(CATEGORY_ID)


def _scenes() -> list[dict]:
    return [{"title": "开场", "description": "镜面反光", "keywords": ["版画"], "duration_sec": 3.0}]


class TestEditAgentInjection:
    @pytest.mark.asyncio
    async def test_creative_prompt_reaches_edit_agent(self, monkeypatch) -> None:
        """V45 A/B 发现：创作提示词此前只进 structure/requirements，剪辑阶段看不到。"""
        from clipwright.agents.edit_agent import EditAgent

        agent = EditAgent()
        captured: dict[str, str] = {}

        async def _fake_structured_output(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            return {
                "base_shot_ms": 3000,
                "transition_weights": {"hard_cut": 1.0},
                "pip_scenes": [],
                "pacing_notes": ["稳"],
            }

        monkeypatch.setattr(agent._llm, "structured_output", _fake_structured_output)
        monkeypatch.setattr(
            "clipwright.agents.edit_agent.settings",
            type("S", (), {"llm_api_key": "test-key"})(),
        )
        await agent._llm_decide_edit_profile(
            _scenes(), {}, "vlog_daily",
            extra_params={"creative_prompt": "硬切为主，不要花哨转场"},
        )
        assert "硬切为主，不要花哨转场" in captured["system_prompt"]

    @pytest.mark.asyncio
    async def test_edit_prompt_carries_identity_and_stage(self, probe_category, monkeypatch) -> None:
        from clipwright.agents.edit_agent import EditAgent

        agent = EditAgent()
        captured: dict[str, str] = {}

        async def _fake_structured_output(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            return {
                "base_shot_ms": 3000,
                "transition_weights": {"hard_cut": 1.0},
                "pip_scenes": [],
                "pacing_notes": ["稳"],
            }

        monkeypatch.setattr(agent._llm, "structured_output", _fake_structured_output)
        monkeypatch.setattr(
            "clipwright.agents.edit_agent.settings",
            type("S", (), {"llm_api_key": "test-key"})(),
        )
        profile = await agent._llm_decide_edit_profile(_scenes(), {}, probe_category)
        assert profile is not None
        prompt = captured["system_prompt"]
        assert "身份印记：整片是一台印钞机" in prompt
        assert "剪辑印记：每 2.4 秒一刀" in prompt
        assert "禁止弹跳动画" in prompt
        # 固定序：身份在阶段规范之前
        assert prompt.index("视频类型身份") < prompt.index("本阶段类型规范（edit）")


class TestAudioAgentInjection:
    @pytest.mark.asyncio
    async def test_audio_prompt_carries_audio_and_subtitle_slots(self, probe_category, monkeypatch) -> None:
        from clipwright.agents.audio_agent import AudioAgent

        agent = AudioAgent()
        captured: dict[str, str] = {}

        async def _fake_structured_output(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            return {"allocations": []}

        monkeypatch.setattr(agent._llm, "structured_output", _fake_structured_output)
        monkeypatch.setattr(
            "clipwright.agents.audio_agent.settings",
            type("S", (), {"llm_api_key": "k", "llm_flash_api_key": "k"})(),
        )
        await agent._llm_match_bgm(
            [{"index": 0, "title": "开场", "description": "暗场", "emotion": "紧张"}],
            {"intro": {"start": 0.0, "end": 3.0}},
            "p" * 8,
            probe_category,
        )
        prompt = captured["system_prompt"]
        assert "音频印记：鼓点卡在呼吸上" in prompt
        assert "字幕印记：关键词加粗描红" in prompt


class TestAnimationStageInjection:
    """animation 槽位进 MG 提示词（动画 Agent → MGGenerator 上下文段落）。"""

    def test_mg_context_section_contains_stage_prompt(self) -> None:
        from clipwright.animation.mg.generator import MGGenerator

        text = MGGenerator._build_context_section(
            {},
            {"stage_prompt": "动画印记：印痕逐层叠上\n禁止弹跳入场"},
        )
        assert "动画印记：印痕逐层叠上" in text
        assert "禁止弹跳入场" in text
        assert "本阶段类型规范" in text

    def test_mg_context_section_without_stage_prompt(self) -> None:
        from clipwright.animation.mg.generator import MGGenerator

        text = MGGenerator._build_context_section({}, {"plugin_id": "x"})
        assert "本阶段类型规范" not in text


class TestQualityAgentInjection:
    @pytest.mark.asyncio
    async def test_quality_prompt_carries_quality_slot(self, probe_category, monkeypatch) -> None:
        from clipwright.agents.quality_agent import QualityAgent
        from clipwright.schema.agent import AgentContext
        from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
        from clipwright.services.llm import LLMService

        captured: dict[str, str] = {}

        async def _fake_structured_output(self, **kwargs):  # noqa: ANN001, ANN201
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            return {"issues": []}

        async def _fake_unified(name, coro_factory, **_kwargs):  # noqa: ANN001, ANN201
            return await coro_factory()

        monkeypatch.setattr(LLMService, "structured_output", _fake_structured_output)
        from clipwright.agents import base as agent_base

        monkeypatch.setattr(agent_base, "unified_llm_call", _fake_unified)

        timeline = Timeline(
            id="tl", width=1920, height=1080, fps=30,
            tracks=[Track(id="t1", name="视频轨", kind=ClipKind.VIDEO, index=0,
                          clips=[Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a.mp4",
                                      track_id="t1", start_sec=0.0,
                                      duration_sec=1.0, text="荧光绿的字")])],
        )
        context = AgentContext(
            pipeline_id="p" * 16, persona_id="demo",
            category_plugin_id=probe_category, topic="示范",
        )
        agent = QualityAgent()
        await agent._check_semantic_qa(timeline, {"overview": "简报"}, context)
        prompt = captured["system_prompt"]
        assert "质检印记：检查是否有荧光绿" in prompt
