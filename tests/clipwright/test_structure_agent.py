"""StructureAgent 动画标记引导测试（T6：强化 mg_dynamic 标记引导）。"""

from __future__ import annotations

from typing import Any

from clipwright.agents.structure_agent import StructureAgent
from clipwright.schema.agent import AgentContext


def _agent() -> StructureAgent:
    return StructureAgent()


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="t6-test",
        persona_id="p_test",
        category_plugin_id="",
        topic="测试选题",
    )


class FakeLLM:
    """记录 structured_output 调用并返回预设 markers。"""

    def __init__(self, markers: list[dict] | None = None) -> None:
        self.markers = markers or []
        self.called = False
        self.system_prompt = ""
        self.user_prompt = ""

    async def structured_output(self, **kwargs: Any) -> dict:
        self.called = True
        self.system_prompt = kwargs.get("system_prompt", "")
        self.user_prompt = kwargs.get("user_prompt", "")
        return {"markers": self.markers}


class TestBuildAnimGuide:
    """_build_anim_guide 输出包含 mg_dynamic 引导。"""

    def test_contains_mg_dynamic_marker(self) -> None:
        """引导中包含 mg_dynamic 标记。"""
        guide = _agent()._build_anim_guide()
        assert "mg_dynamic" in guide

    def test_contains_payload_shape(self) -> None:
        """payload 结构：description / text（| 分隔）/ style / 可选 data 数组。"""
        guide = _agent()._build_anim_guide()
        assert '"description"' in guide
        assert '"text"' in guide
        assert '"style"' in guide
        assert '"data"' in guide
        assert "|" in guide

    def test_contains_rules(self) -> None:
        """规则：纯文字强调用 [文字动画]、长文本走字幕、每视频鼓励 1-2 个 MG。"""
        guide = _agent()._build_anim_guide()
        assert "[文字动画]" in guide
        assert "字幕" in guide
        assert "1-2" in guide


class TestEnrichSceneAnimations:
    """_enrich_scene_animations 既有行为不回归。"""

    async def test_unmarked_scene_goes_through_llm(self) -> None:
        """无标记场景仍走 LLM enrich 路径（回归保护）。"""
        agent = _agent()
        fake = FakeLLM(markers=[])
        agent._llm = fake  # type: ignore[assignment]
        scenes = [{"title": "S1", "description": "没有任何动画标记的描述", "duration_sec": 10}]
        out = await agent._enrich_scene_animations(scenes, _context())
        assert fake.called is True
        assert out == scenes

    async def test_marked_scene_skips_llm(self) -> None:
        """已有动画标记的场景跳过 LLM enrich。"""
        agent = _agent()
        fake = FakeLLM(markers=[])
        agent._llm = fake  # type: ignore[assignment]
        scenes = [{
            "title": "S1",
            "description": "强调 [文字动画]淡入：关键结论",
            "duration_sec": 10,
        }]
        out = await agent._enrich_scene_animations(scenes, _context())
        assert fake.called is False
        assert out == scenes

    async def test_llm_marker_appended(self) -> None:
        """LLM 返回的 mg_dynamic 标记被追加到 description。"""
        agent = _agent()
        fake = FakeLLM(markers=[{
            "index": 0,
            "animation_marker": (
                '[逻辑动画]mg_dynamic:{"description":"增长柱状图",'
                '"text":"2023|2024|2025","style":"tech_dark"}'
            ),
        }])
        agent._llm = fake  # type: ignore[assignment]
        scenes = [{"title": "S1", "description": "近三年营收数据", "duration_sec": 10}]
        out = await agent._enrich_scene_animations(scenes, _context())
        assert fake.called is True
        assert "mg_dynamic" in out[0]["description"]

    async def test_enrich_system_prompt_synced(self) -> None:
        """enrich 的 system prompt 包含与 _build_anim_guide 一致的 mg_dynamic payload 引导。"""
        agent = _agent()
        fake = FakeLLM(markers=[])
        agent._llm = fake  # type: ignore[assignment]
        scenes = [{"title": "S1", "description": "无标记", "duration_sec": 10}]
        await agent._enrich_scene_animations(scenes, _context())
        guide = agent._build_anim_guide()
        assert fake.called is True
        for token in ("mg_dynamic", '"description"', '"text"', '"style"', '"data"', "字幕"):
            assert token in fake.system_prompt, f"system prompt 缺少 {token}"
            assert token in guide, f"anim guide 缺少 {token}"
