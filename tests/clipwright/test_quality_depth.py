"""C3: 质检深度默认策略（quality_depth: basic/standard/deep）测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.quality_agent import QualityAgent
from clipwright.schema.agent import AgentContext, QualityInput
from clipwright.schema.timeline import Clip, Timeline, Track


def _timeline() -> Timeline:
    return Timeline(
        id="tl_depth", width=320, height=240, fps=10, duration_sec=60,
        tracks=[
            Track(id="v1", name="V1", kind="video", index=0, clips=[
                Clip(id="c1", kind="video", asset_id="a1", track_id="v1",
                     start_sec=0, duration_sec=5,
                     metadata={"description": "城市夜景", "local_path": "/tmp/clip.mp4"}),
            ]),
            Track(id="a1", name="A1", kind="audio", index=1, clips=[]),
        ],
    )


def _ctx() -> AgentContext:
    return AgentContext(
        pipeline_id="quality_depth_test",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
    )


def _input(constraints: dict) -> QualityInput:
    return QualityInput(
        context=_ctx(),
        timeline=_timeline(),
        constraints=constraints,
    )


class TestQualityDepth:
    @pytest.mark.asyncio
    async def test_basic_depth_disables_visual_and_semantic(self) -> None:
        """basic：即使 enable_visual_llm/enable_semantic_qa 开启，也不执行媒体/LLM 检查。"""
        agent = QualityAgent()
        with (
            patch("clipwright.agents.quality_agent.QualityAgent._check_frame_matches",
                  new=AsyncMock(return_value=[])) as frame,
            patch("clipwright.agents.quality_agent.QualityAgent._check_semantic_qa",
                  new=AsyncMock(return_value=[])) as sema,
        ):
            out = await agent.execute(
                _input({"quality_depth": "basic", "enable_visual_llm": True, "enable_semantic_qa": True}),
                _ctx(),
            )
        frame.assert_not_awaited()
        sema.assert_not_awaited()
        assert out.decision.value in ("pass", "fail")

    @pytest.mark.asyncio
    async def test_deep_depth_forces_visual_and_semantic(self) -> None:
        """deep：即使开关默认关闭，也强制执行帧匹配 + 语义质检。"""
        agent = QualityAgent()
        with (
            patch("clipwright.agents.quality_agent.QualityAgent._check_frame_matches",
                  new=AsyncMock(return_value=[])) as frame,
            patch("clipwright.agents.quality_agent.QualityAgent._check_semantic_qa",
                  new=AsyncMock(return_value=[])) as sema,
        ):
            out = await agent.execute(_input({"quality_depth": "deep"}), _ctx())
        frame.assert_awaited_once()
        sema.assert_awaited_once()
        assert out.decision.value in ("pass", "fail")

    @pytest.mark.asyncio
    async def test_standard_depth_keeps_gates_off_by_default(self) -> None:
        """standard（默认）：不显式开启时不执行视觉/语义检查。"""
        agent = QualityAgent()
        with (
            patch("clipwright.agents.quality_agent.QualityAgent._check_frame_matches",
                  new=AsyncMock(return_value=[])) as frame,
            patch("clipwright.agents.quality_agent.QualityAgent._check_semantic_qa",
                  new=AsyncMock(return_value=[])) as sema,
        ):
            out = await agent.execute(_input({}), _ctx())
        frame.assert_not_awaited()
        sema.assert_not_awaited()
        assert out.decision.value in ("pass", "fail")

    @pytest.mark.asyncio
    async def test_standard_depth_with_explicit_gate_enables_visual(self) -> None:
        """standard + enable_visual_llm=True → 帧检查执行（原有行为保留）。"""
        agent = QualityAgent()
        with (
            patch("clipwright.agents.quality_agent.QualityAgent._check_frame_matches",
                  new=AsyncMock(return_value=[])) as frame,
            patch("clipwright.agents.quality_agent.QualityAgent._check_semantic_qa",
                  new=AsyncMock(return_value=[])) as sema,
        ):
            out = await agent.execute(_input({"enable_visual_llm": True}), _ctx())
        frame.assert_awaited_once()
        sema.assert_not_awaited()
        assert out.decision.value in ("pass", "fail")
