"""动画阶段必须在任务事件流里上报文字/过渡动画进度。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.agent import AgentContext, AnimationInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


def _ctx() -> AgentContext:
    return AgentContext(
        pipeline_id="task_anim_events", persona_id="p", category_plugin_id="",
        topic="text animation event test", extra_params={},
    )


def _timeline() -> Timeline:
    tl = Timeline()
    tl.tracks.append(Track(
        id="v", name="视频轨", kind=ClipKind.VIDEO, index=0,
        clips=[Clip(
            id="clip_text", kind=ClipKind.VIDEO, asset_id="a1", track_id="v",
            start_sec=0.0, duration_sec=4.0,
            metadata={"description": "【文字动画】淡入：这是一段测试文字"},
        )],
    ))
    return tl


@pytest.mark.asyncio
async def test_text_animation_emits_task_progress() -> None:
    agent = AnimationAgent()
    ctx = _ctx()
    seen: list[str] = []

    def fake_progress(message, _detail=None):
        seen.append(str(message))
        return True

    with (
        patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
        patch.object(AnimationAgent, "_search_library_images", new=AsyncMock(return_value=[])),
        patch.object(AnimationAgent, "_handle_text_animation_async", new=AsyncMock()),
        patch("clipwright.agents.animation_agent._emit_progress", side_effect=fake_progress),
    ):
        out = await agent.execute(AnimationInput(context=ctx, timeline=_timeline()), ctx)

    assert any("文字动画 1 开始" in m for m in seen), seen
    assert any("文字动画 1 完成" in m for m in seen), seen
    assert out.timeline is not None
