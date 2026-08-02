"""Integration tests for vision-LLM material scoring and configuration routing."""

from __future__ import annotations

import json
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

from clipwright.agents.material_agent import MaterialAgent, _text_relevance_score
from clipwright.config import settings
from clipwright.schema.agent import AgentContext, AgentDecision, MaterialInput
from clipwright.schema.material import MaterialAsset, MaterialSearchResult
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
from clipwright.tool.frame_extractor import extract_frames
from clipwright.tool.stubs import VisionLLMTool


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


def _vision_result(score: float = 0.7) -> ToolExecResult:
    return ToolExecResult(
        status=ToolStatus.SUCCESS,
        tool_name="vision_llm",
        output={
            "score": score,
            "extraction_method": "thumbnail",
            "frames_analyzed": 1,
        },
    )


@pytest.mark.asyncio
async def test_default_vision_llm_on_when_llm_configured() -> None:
    """未显式配置插件开关时，LLM 已配置（如 Ollama base_url）→ 默认开启视觉 LLM。"""
    context = _context()
    input_data = _material_input("default-on-scene", {})

    with (
        patch.object(settings, "llm_api_key", ""),
        patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
        patch.object(settings, "vision_llm_api_key", None),
        patch.object(settings, "vision_llm_base_url", None),
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
            new=AsyncMock(return_value=["default-on-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(return_value=_vision_result()),
        ) as vision_execute,
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    assert any("视觉LLM分析已启用" in note for note in output.material_notes)
    vision_execute.assert_awaited()


@pytest.mark.asyncio
async def test_default_vision_llm_off_when_llm_unconfigured() -> None:
    """未显式配置插件开关且未配置任何 LLM → 默认关闭视觉 LLM。"""
    context = _context()

    with (
        patch.object(settings, "llm_api_key", ""),
        patch.object(settings, "llm_base_url", None),
        patch.object(settings, "vision_llm_api_key", None),
        patch.object(settings, "vision_llm_base_url", None),
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
            new=AsyncMock(return_value=["default-off-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.5),
        ),
    ):
        output = await MaterialAgent().execute(_material_input("default-off-scene", {}), context)

    assert output.decision == AgentDecision.PASS
    assert not any("视觉LLM分析已启用" in note for note in output.material_notes)


@pytest.mark.asyncio
async def test_explicit_plugin_config_overrides_llm_default() -> None:
    """显式插件配置优先于 LLM 配置默认值（双向覆盖）。"""
    context = _context()

    # LLM 已配置但显式关闭 → 关闭
    with (
        patch.object(settings, "llm_api_key", ""),
        patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
        patch.object(settings, "vision_llm_api_key", None),
        patch.object(settings, "vision_llm_base_url", None),
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
            new=AsyncMock(return_value=["explicit-off-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.5),
        ),
    ):
        off_output = await MaterialAgent().execute(
            _material_input("explicit-off-scene", {"enable_visual_llm": False}), context
        )
    assert not any("视觉LLM分析已启用" in note for note in off_output.material_notes)

    # LLM 未配置但显式开启 → 开启
    with (
        patch.object(settings, "llm_api_key", ""),
        patch.object(settings, "llm_base_url", None),
        patch.object(settings, "vision_llm_api_key", None),
        patch.object(settings, "vision_llm_base_url", None),
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
            new=AsyncMock(return_value=["explicit-on-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(return_value=_vision_result()),
        ) as vision_execute,
    ):
        on_output = await MaterialAgent().execute(
            _material_input("explicit-on-scene", {"enable_visual_llm": True}), context
        )
    assert any("视觉LLM分析已启用" in note for note in on_output.material_notes)
    vision_execute.assert_awaited()


@pytest.mark.asyncio
async def test_vision_tool_receives_scene_narration_only() -> None:
    """vision 工具接收的是场景级旁白 + 素材意图，而非整脚本旁白。"""
    context = _context()
    context.extra_params["script_text"] = "完整脚本文本：包含场景A和场景B的全部旁白内容"
    input_data = MaterialInput(
        context=context,
        script_skeleton={
            "scenes": [
                {
                    "title": "narration-scene",
                    "keywords": ["城市", "夜景"],
                    "description": "场景A画面描述",
                    "voiceover_script": "场景A口播旁白",
                    "visual_description": {
                        "material_content": "键盘打字的特写",
                        "material_preference": "冷色调",
                    },
                },
                {
                    "title": "other-scene",
                    "keywords": ["城市"],
                    "description": "场景B画面描述",
                    "voiceover_script": "场景B口播旁白",
                },
            ],
        },
        material_plugin_config={"enable_visual_llm": True},
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
            new=AsyncMock(return_value=["narration-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(return_value=_vision_result()),
        ) as vision_execute,
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    scene_payloads = [
        call.kwargs["scene_context"]
        for call in vision_execute.await_args_list
        if call.kwargs.get("scene_context", {}).get("title") == "narration-scene"
    ]
    assert scene_payloads
    ctx = scene_payloads[0]
    assert ctx["narration_text"] == "场景A口播旁白"
    assert ctx["material_intent"] == "键盘打字的特写 冷色调"
    assert "场景B口播旁白" not in ctx["narration_text"]
    assert "完整脚本文本" not in json.dumps(ctx, ensure_ascii=False)


@pytest.mark.asyncio
async def test_scoring_sensitive_to_narration_overlap(tmp_path: Path) -> None:
    """场景旁白词（如"键盘打字"）与帧描述重叠 → 分数显著高于无旁白。"""
    analyses = [
        {"description": "键盘打字的特写镜头", "tags": ["键盘"], "labels": []},
    ]

    async def run_tool(narration: str) -> float:
        frame_path = tmp_path / f"frame-{len(narration)}.jpg"
        frame_path.write_bytes(b"junk")
        with (
            patch(
                "clipwright.tool.frame_extractor.extract_frames",
                new=AsyncMock(return_value=[str(frame_path)]),
            ),
            patch(
                "clipwright.services.vision.VisionService.analyze_image",
                new=AsyncMock(return_value=analyses[0]),
            ),
        ):
            result = await VisionLLMTool().execute(
                asset={"url": "https://example.com/video.mp4", "duration_sec": 30},
                scene_context={
                    "title": "narration-overlap",
                    "keywords": ["键盘"],
                    "description": "",
                    "narration_text": narration,
                    "material_intent": "",
                },
                frame_count=1,
            )
        return float((result.output or {})["score"])

    with_narration = await run_tool("键盘打字")
    without_narration = await run_tool("")

    assert with_narration > without_narration
    assert with_narration >= 0.9


@pytest.mark.asyncio
async def test_vision_tool_graceful_fallback_on_analyze_error(tmp_path: Path) -> None:
    """malformed_input：视觉分析抛错 → 回退 0.5，不向上传播异常。"""
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"junk")

    with (
        patch(
            "clipwright.tool.frame_extractor.extract_frames",
            new=AsyncMock(return_value=[str(frame)]),
        ),
        patch(
            "clipwright.services.vision.VisionService.analyze_image",
            new=AsyncMock(side_effect=RuntimeError("vision model crash")),
        ),
    ):
        result = await VisionLLMTool().execute(
            asset={"url": "https://example.com/video.mp4", "duration_sec": 30},
            scene_context={"title": "malformed", "keywords": [], "description": ""},
            frame_count=1,
        )

    output = result.output or {}
    assert output["score"] == 0.5
    assert output.get("fallback") is True


# ── Todo 8 (C4b): 无视觉时的文本相关性兜底打分 ──


def test_text_relevance_score_related_narration() -> None:
    """(a) 场景旁白「城市夜景」+ 素材标题含「夜景」→ text_score > 0.5。"""
    asset = MaterialAsset(
        id="rel-1",
        title="城市夜景延时摄影",
        url="https://example.com/rel.mp4",
        duration_sec=10,
        tags=["夜景", "城市"],
    )
    score = _text_relevance_score("城市夜景", "", [], asset)
    assert score > 0.5


def test_text_relevance_score_unrelated() -> None:
    """(b) 不相关（「美食」vs「编程」）→ text_score < 0.3。"""
    asset = MaterialAsset(
        id="unrel-1",
        title="编程开发教程",
        url="https://example.com/unrel.mp4",
        duration_sec=10,
        tags=["编程", "代码"],
    )
    score = _text_relevance_score("美食推荐", "", [], asset)
    assert score < 0.3


def test_text_relevance_score_empty_vocab_safe() -> None:
    """(d) 空/None 词表不崩溃（adversarial），返回 0.0。"""
    assert _text_relevance_score("", "", [], {"title": None, "tags": None}) == 0.0
    assert _text_relevance_score(None, None, None, {"title": None, "tags": None}) == 0.0
    assert _text_relevance_score("城市夜景", "", [], {"title": "", "tags": []}) == 0.0


def _two_result_search(
    rel_title: str,
    rel_tags: list[str],
    unrel_title: str,
    unrel_tags: list[str],
) -> list[MaterialSearchResult]:
    return [
        MaterialSearchResult(
            asset=MaterialAsset(
                id="rel-id",
                title=rel_title,
                url="https://example.com/rel.mp4",
                duration_sec=10,
                resolution="1920x1080",
                tags=rel_tags,
            ),
            score=0.5,
            source_name="test_source",
        ),
        MaterialSearchResult(
            asset=MaterialAsset(
                id="unrel-id",
                title=unrel_title,
                url="https://example.com/unrel.mp4",
                duration_sec=10,
                resolution="1920x1080",
                tags=unrel_tags,
            ),
            score=0.5,
            source_name="test_source",
        ),
    ]


@pytest.mark.asyncio
async def test_text_fallback_orders_related_asset_first() -> None:
    """(c) 无视觉（帧校验占位 0.5）时，文本相关性兜底参与排序 → 相关素材在前。"""
    context = _context()
    input_data = MaterialInput(
        context=context,
        script_skeleton={
            "scenes": [{
                "title": "fallback-order-scene",
                "keywords": [],
                "description": "",
                "voiceover_script": "城市夜景中的车流",
            }],
        },
        material_plugin_config={"enable_visual_llm": False},
    )
    results = _two_result_search(
        "城市夜景航拍", ["城市", "夜景"],
        "编程开发教程", ["编程", "代码"],
    )
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.search",
            new=AsyncMock(return_value=results),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["text-fallback-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.5),
        ),
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    titles = [
        asset["title"]
        for clip in output.candidate_clips
        for asset in clip["suggested_assets"]
    ]
    assert titles
    assert titles[0] == "城市夜景航拍"


@pytest.mark.asyncio
async def test_text_fallback_all_zero_keeps_original_order() -> None:
    """(d) 全候选 0 匹配（空词表）→ 退化为原顺序，不抛异常。"""
    context = _context()
    input_data = MaterialInput(
        context=context,
        script_skeleton={
            "scenes": [{
                "title": "empty-vocab-scene",
                "keywords": [],
                "description": "",
                "voiceover_script": "",
            }],
        },
        material_plugin_config={"enable_visual_llm": False},
    )
    results = _two_result_search(
        "编程开发教程", ["编程"],
        "甜品烘焙指南", ["甜品"],
    )
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.search",
            new=AsyncMock(return_value=results),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["empty-vocab-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.5),
        ),
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    titles = [
        asset["title"]
        for clip in output.candidate_clips
        for asset in clip["suggested_assets"]
    ]
    assert titles == ["编程开发教程", "甜品烘焙指南"]


# ── Todo 14 (C4c): 素材不符时的有界重选循环 ──


def _round_two_results() -> list[MaterialSearchResult]:
    return [
        MaterialSearchResult(
            asset=MaterialAsset(
                id="asset-best",
                title="完美夜景素材",
                url="https://example.com/best.mp4",
                duration_sec=30,
                resolution="1920x1080",
                tags=["夜景", "车流"],
            ),
            score=0.9,
            source_name="test_source",
        ),
    ]


def _reselect_input(title: str) -> MaterialInput:
    return MaterialInput(
        context=_context(),
        script_skeleton={
            "scenes": [{
                "title": title,
                "keywords": [],
                "description": "",
                "voiceover_script": "夜景车流中的城市",
                "visual_description": {
                    "material_content": "城市夜景",
                    "material_preference": "冷色调",
                },
            }],
        },
        material_plugin_config={"enable_visual_llm": False},
    )


@pytest.mark.asyncio
async def test_reselect_triggers_on_low_match_and_improves() -> None:
    """(a) 首轮全 0.2 分（低于阈值）→ 触发一次重选 → 第二轮 0.8 候选被采纳 + notes 含 reselect。"""
    input_data = _reselect_input("low-match-scene")
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["round-one-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(side_effect=[_search_results(), _round_two_results()]),
        ) as search_mock,
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(side_effect=[0.2, 0.8]),
        ),
    ):
        output = await MaterialAgent().execute(input_data, _context())

    assert output.decision == AgentDecision.PASS
    assert search_mock.call_count == 2
    clip = output.candidate_clips[0]
    assert clip["reselect"]["triggered"] is True
    assert clip["reselect"]["query"]
    assert clip["reselect"]["improved_score"] == 0.8
    titles = [asset["title"] for asset in clip["suggested_assets"]]
    assert titles[0] == "完美夜景素材"
    assert any("已按文案重选素材" in note for note in output.material_notes)


@pytest.mark.asyncio
async def test_reselect_skipped_when_first_round_above_threshold() -> None:
    """(b) 首轮即达标（0.8 ≥ 阈值）→ 不触发额外搜索（_search_with_cache 调用次数不变）。"""
    input_data = _reselect_input("good-match-scene")
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["round-one-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(return_value=_search_results()),
        ) as search_mock,
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.8),
        ),
    ):
        output = await MaterialAgent().execute(input_data, _context())

    assert output.decision == AgentDecision.PASS
    assert search_mock.call_count == 1
    clip = output.candidate_clips[0]
    assert clip["reselect"]["triggered"] is False
    assert not any("已按文案重选素材" in note for note in output.material_notes)


@pytest.mark.asyncio
async def test_reselect_keeps_original_when_second_round_also_low() -> None:
    """(c) adversarial：重选仍低于阈值 → 保留原最优 + warning note，不抛异常。"""
    input_data = _reselect_input("still-low-scene")
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["round-one-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(side_effect=[_search_results(), _round_two_results()]),
        ) as search_mock,
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(side_effect=[0.2, 0.2]),
        ),
    ):
        output = await MaterialAgent().execute(input_data, _context())

    assert output.decision == AgentDecision.PASS
    assert search_mock.call_count == 2
    clip = output.candidate_clips[0]
    assert clip["reselect"]["triggered"] is True
    assert clip["score"] == 0.2
    titles = [asset["title"] for asset in clip["suggested_assets"]]
    assert titles[0] == "City at night"
    assert any("保持原素材" in note for note in output.material_notes)


@pytest.mark.asyncio
async def test_reselect_noop_when_no_candidates() -> None:
    """(d) 无候选场景 → 不触发重选、不抛异常。"""
    input_data = _reselect_input("no-candidate-scene")
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "test_source"}],
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["round-one-query"]),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(return_value=[]),
        ) as search_mock,
    ):
        output = await MaterialAgent().execute(input_data, _context())

    assert output.decision == AgentDecision.PASS
    assert search_mock.call_count == 1
    clip = output.candidate_clips[0]
    assert clip["reselect"]["triggered"] is False
    assert not any("已按文案重选素材" in note for note in output.material_notes)
