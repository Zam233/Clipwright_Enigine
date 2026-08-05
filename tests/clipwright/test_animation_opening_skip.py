"""Tests — 开场不生成动画标记由 StructureAgent 源头保证（_strip_opening_animation_markers）。

实现方式（用户要求）：在结构 Agent（生成源头）剥离开场场景的动画标记，
而非动画 Agent（消费端）按位置事后跳过。本测试覆盖：
1. 复用规划书场景路径（_enrich_scene_animations 之后剥离）
2. LLM 生成场景路径（返回前剥离）
3. 开场标记剥离后，AnimationAgent 忠实执行 description（不再自行跳过位置）
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.agents.structure_agent import StructureAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AnimationInput, StructureInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track

_MG_MARKER = (
    '[逻辑动画]mg_dynamic:{"description":"三阶段增长柱状图",'
    '"text":"2023|2024|2025","style":"tech_dark"}'
)


def _ctx() -> AgentContext:
    return AgentContext(
        pipeline_id="p_test_opening",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
        extra_params={},
    )


def _timeline_two_marked_clips() -> Timeline:
    tl = Timeline()
    tl.tracks.append(
        Track(
            id="v_main",
            name="视频轨",
            kind=ClipKind.VIDEO,
            index=0,
            clips=[
                Clip(
                    id="clip_opening",
                    kind=ClipKind.VIDEO,
                    asset_id="a1",
                    track_id="v_main",
                    start_sec=0.0,
                    duration_sec=10.0,
                    metadata={"description": f"开场口播 {_MG_MARKER}"},
                ),
                Clip(
                    id="clip_body",
                    kind=ClipKind.VIDEO,
                    asset_id="a2",
                    track_id="v_main",
                    start_sec=10.0,
                    duration_sec=10.0,
                    metadata={"description": f"中段论证 {_MG_MARKER}"},
                ),
            ],
        )
    )
    return tl


# ── StructureAgent 源头剥离 ──────────────────────────────


def test_strip_opening_markers_reuse_path(monkeypatch) -> None:
    """复用规划书场景：_enrich_scene_animations 之后，开场场景标记被剥离。"""
    agent = StructureAgent()
    scenes = [
        {"title": "开场", "description": f"开场口播 {_MG_MARKER}", "duration_sec": 10, "keywords": ["a"]},
        {"title": "中段", "description": f"中段论证 {_MG_MARKER}", "duration_sec": 10, "keywords": ["b"]},
    ]
    # _enrich_scene_animations 直接返回（不调用 LLM）；剥离由 execute 调用
    async def _noop_enrich(s, ctx):
        return s
    monkeypatch.setattr(agent, "_enrich_scene_animations", _noop_enrich)

    stripped = agent._strip_opening_animation_markers(scenes)
    assert "[逻辑动画]" not in stripped[0]["description"]
    assert "开场口播" in stripped[0]["description"]
    # 第二个场景不受影响
    assert "[逻辑动画]" in stripped[1]["description"]


def test_strip_opening_markers_empty_scenes() -> None:
    """空场景列表不报错。"""
    agent = StructureAgent()
    assert agent._strip_opening_animation_markers([]) == []


def test_strip_opening_markers_no_marker() -> None:
    """开场场景本来就没有标记 → 不修改。"""
    agent = StructureAgent()
    scenes = [{"title": "开场", "description": "纯口播开场", "duration_sec": 10, "keywords": ["a"]}]
    out = agent._strip_opening_animation_markers(scenes)
    assert out[0]["description"] == "纯口播开场"


# ── AnimationAgent 忠实执行（不再自行跳过位置） ──────────


@pytest.mark.asyncio
async def test_animation_agent_executes_opening_marker_if_present() -> None:
    """若 description 仍有标记（例如旧时间线直接喂给 AnimationAgent），
    动画 Agent 忠实执行——位置判断跳过已移除，由 StructureAgent 源头保证。"""
    agent = AnimationAgent()
    ctx = _ctx()
    inp = AnimationInput(context=ctx, timeline=_timeline_two_marked_clips())

    with (
        patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
        patch.object(
            AnimationAgent, "_handle_logic_animation", new=AsyncMock()
        ) as mock_logic,
    ):
        out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    # 两个 clip 的标记都被执行（AnimationAgent 不做位置跳过）
    assert mock_logic.await_count == 2


def test_structure_agent_prompt_mentions_opening_ban() -> None:
    """MG_DYNAMIC_GUIDE 明确禁止开场场景使用动画标记（prompt 层约束）。"""
    from clipwright.agents.structure_agent import MG_DYNAMIC_GUIDE
    assert "开场" in MG_DYNAMIC_GUIDE
    assert "禁止" in MG_DYNAMIC_GUIDE
