"""Integration tests for vision-LLM material scoring and configuration routing."""

from __future__ import annotations

import sys
from types import ModuleType
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _IsoBaseClient:
    """Import-only stand-in for the optional IsoBase client dependency."""


class _LLMResponse:
    """Import-only stand-in for IsoBase's response model."""


if "isobase" not in sys.modules:
    isobase_module = ModuleType("isobase")
    llm_module = ModuleType("isobase.llm")
    entities_module = ModuleType("isobase.llm.entities")
    llm_module.AnthropicMessages = _IsoBaseClient
    llm_module.OpenAIChat = _IsoBaseClient
    entities_module.LLMResponse = _LLMResponse
    isobase_module.llm = llm_module
    sys.modules["isobase"] = isobase_module
    sys.modules["isobase.llm"] = llm_module
    sys.modules["isobase.llm.entities"] = entities_module

from clipwright.agents.material_agent import MaterialAgent
from clipwright.schema.agent import AgentContext, AgentDecision, MaterialInput
from clipwright.schema.material import MaterialAsset, MaterialSearchResult
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
from clipwright.tool.frame_extractor import extract_frames


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="vision_material_test",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
    )


def _material_input(
    title: str,
    material_plugin_config: dict[str, bool | int],
) -> MaterialInput:
    context = _context()
    return MaterialInput(
        context=context,
        script_skeleton={
            "scenes": [
                {
                    "title": title,
                    "keywords": ["城市", "夜景"],
                    "description": "城市夜景中的车流",
                },
            ],
        },
        material_plugin_config=material_plugin_config,
    )


def _search_results() -> list[MaterialSearchResult]:
    return [
        MaterialSearchResult(
            asset=MaterialAsset(
                id="asset-1",
                title="City at night",
                url="https://example.com/video.mp4",
                duration_sec=30,
                resolution="1920x1080",
                tags=["城市", "夜景"],
            ),
            score=0.9,
            source_name="test_source",
        ),
    ]


def _all_candidate_scores(output: object) -> list[float]:
    candidate_clips = output.candidate_clips
    return [
        score
        for candidate in candidate_clips
        for score in (
            candidate["score"],
            *(asset["score"] for asset in candidate["suggested_assets"]),
        )
    ]


@pytest.mark.asyncio
async def test_toggle_off_uses_dummy_score() -> None:
    context = _context()
    input_data = _material_input(
        "toggle-off-scene", {"enable_visual_llm": False}
    )

    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.search",
            new=AsyncMock(return_value=_search_results()),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["toggle-off-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.5),
        ),
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    assert _all_candidate_scores(output)
    assert all(score == 0.5 for score in _all_candidate_scores(output))
    assert not any("视觉LLM分析已启用" in note for note in output.material_notes)


@pytest.mark.asyncio
async def test_toggle_on_calls_vision_tool() -> None:
    context = _context()
    input_data = _material_input(
        "toggle-on-scene",
        {"enable_visual_llm": True, "visual_llm_frame_count": 2},
    )
    vision_result = ToolExecResult(
        status=ToolStatus.SUCCESS,
        tool_name="vision_llm",
        output={
            "score": 0.85,
            "extraction_method": "thumbnail",
            "frames_analyzed": 1,
        },
    )

    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.search",
            new=AsyncMock(return_value=_search_results()),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["toggle-on-query"]),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(return_value=vision_result),
        ) as vision_execute,
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    assert any(score != 0.5 for score in _all_candidate_scores(output))
    assert any("视觉LLM分析已启用" in note for note in output.material_notes)
    vision_execute.assert_awaited()
    assert vision_execute.await_args.kwargs["frame_count"] == 2


@pytest.mark.asyncio
async def test_vision_tool_fallback() -> None:
    context = _context()
    input_data = _material_input(
        "vision-fallback-scene", {"enable_visual_llm": True}
    )

    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.search",
            new=AsyncMock(return_value=_search_results()),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["vision-fallback-query"]),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(side_effect=RuntimeError("vision unavailable")),
        ),
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    assert _all_candidate_scores(output)
    assert all(score == 0.5 for score in _all_candidate_scores(output))


@pytest.mark.asyncio
async def test_extract_frames_tiers(tmp_path: Path) -> None:
    response = MagicMock()
    response.content = b"image-bytes"
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "clipwright.tool.frame_extractor.httpx.AsyncClient",
        return_value=client_context,
    ):
        thumbnail_frames = await extract_frames(
            {"thumbnail_url": "https://example.com/thumb.jpg"},
            temp_dir=str(tmp_path),
        )

    assert len(thumbnail_frames) == 1
    assert Path(thumbnail_frames[0]).read_bytes() == b"image-bytes"

    with patch(
        "clipwright.tool.frame_extractor._extract_from_source",
        new=AsyncMock(return_value=[str(tmp_path / "remote.jpg")]),
    ) as extract_remote:
        remote_frames = await extract_frames(
            {"url": "https://example.com/video.mp4", "duration_sec": 30},
            frame_count=2,
            temp_dir=str(tmp_path),
        )

    assert remote_frames == [str(tmp_path / "remote.jpg")]
    extract_remote.assert_awaited_once_with(
        "https://example.com/video.mp4", 30.0, 2, tmp_path
    )

    with (
        patch("clipwright.tool.frame_extractor.Path.is_file", return_value=True),
        patch(
            "clipwright.tool.frame_extractor._extract_from_source",
            new=AsyncMock(return_value=[str(tmp_path / "local.jpg")]),
        ) as extract_local,
    ):
        local_frames = await extract_frames(
            {"local_path": "/tmp/test.mp4", "duration_sec": 30},
            frame_count=2,
            temp_dir=str(tmp_path),
        )

    assert local_frames == [str(tmp_path / "local.jpg")]
    extract_local.assert_awaited_once_with("/tmp/test.mp4", 30.0, 2, tmp_path)
    assert await extract_frames({}, temp_dir=str(tmp_path)) == []


def test_config_routing() -> None:
    orchestrator = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    plugin = MagicMock()
    plugin.config = {"enable_visual_llm": True}

    configured = orchestrator._build_input("material", {}, {}, plugin)
    unconfigured = orchestrator._build_input("material", {}, {}, None)

    assert configured["material_plugin_config"] == {"enable_visual_llm": True}
    assert unconfigured["material_plugin_config"] == {}
