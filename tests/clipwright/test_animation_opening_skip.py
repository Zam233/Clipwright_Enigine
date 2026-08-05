"""Tests for T4 — AnimationAgent skips logic/text markers on the opening (first) video clip."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AnimationInput
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


@pytest.mark.asyncio
async def test_animation_agent_skips_first_clip_logic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第一个 video clip 的 [逻辑动画] 标记被跳过，第二个 clip 正常生成动画。"""
    agent = AnimationAgent()
    ctx = _ctx()
    inp = AnimationInput(context=ctx, timeline=_timeline_two_marked_clips())

    with (
        patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
        patch.object(
            AnimationAgent, "_handle_logic_animation", new=AsyncMock()
        ) as mock_logic,
    ):
        with caplog.at_level(logging.WARNING, logger="clipwright"):
            out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    # 只有第二个 clip 的逻辑动画被执行
    assert mock_logic.await_count == 1
    called_clip = mock_logic.await_args.args[1]
    assert called_clip.id == "clip_body"
    # 开场跳过有 warning 日志
    assert any(
        "开场场景跳过" in rec.message and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )
    # 动画轨上没有为开场 clip (start_sec=0) 创建的 clip
    anim_tracks = [t for t in out.timeline.tracks if str(t.kind) == "animation"]
    for t in anim_tracks:
        for c in t.clips:
            assert not (c.start_sec == 0.0 and c.metadata.get("category") in ("logic", "mg", "mg_dynamic"))
