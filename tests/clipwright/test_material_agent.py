"""MaterialAgent 素材校验 + 有界重试测试（T5/C3a）。

覆盖：
- 标题/标签启发式评分（_heuristic_title_match_score / _validate_video_frame）
- gate 关闭时视觉工具不被调用
- 有界重试：换素材 → 换搜索词，最多 2 次
- 换素材路径 / 换搜索词路径
- execute 集成路径
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.material_agent import (
    _heuristic_title_match_score,
    _validate_video_frame,
    MaterialAgent,
)
from clipwright.schema.agent import AgentContext, AgentDecision, MaterialInput
from clipwright.schema.material import MaterialAsset, MaterialSearchResult

SCENE = {
    "title": "城市夜景",
    "keywords": ["城市", "夜景"],
    "description": "城市夜景中的车流",
}


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="test-pipeline",
        persona_id="p_test",
        category_plugin_id="",
        topic="测试",
    )


def _input() -> MaterialInput:
    return MaterialInput(context=_context(), script_skeleton={"scenes": []})


def _asset(
    asset_id: str,
    title: str,
    tags: list[str],
    url: str = "https://example.com/v.mp4",
) -> MaterialSearchResult:
    return MaterialSearchResult(
        asset=MaterialAsset(
            id=asset_id,
            title=title,
            tags=tags,
            url=url,
            duration_sec=10,
            resolution="1920x1080",
        ),
        score=0.9,
        source_name="test_source",
    )


async def _run_scene(
    agent: MaterialAgent,
    scene: dict,
    *,
    use_vision_llm: bool = False,
    batch_query: list[str] | None = ["q1"],
) -> dict:
    return await agent._process_scene(
        i=0,
        scene=scene,
        persona_style_keywords=[],
        brief_material_hint="",
        source_ids=None,
        pref_orientation="landscape",
        use_vision_llm=use_vision_llm,
        vision_frame_count=3,
        input_data=_input(),
        pipeline_id="test-pipeline",
        batch_query=batch_query,
    )


# ── 启发式评分 ──


def test_heuristic_matching_scores_high() -> None:
    """素材标题/标签与场景关键词匹配 → 分数高于阈值 (0.5)。"""
    score = _heuristic_title_match_score(
        "城市夜景 车流", ["城市", "夜景"], "城市夜景 城市 夜景"
    )
    assert score > 0.5


def test_heuristic_mismatch_scores_low() -> None:
    """素材标题与场景关键词完全不匹配 → 分数低于阈值 (0.35)。"""
    score = _heuristic_title_match_score(
        "海边日落", ["海滩", "日落"], "城市夜景 城市 夜景"
    )
    assert score < 0.35


def test_heuristic_empty_asset_text_scores_zero() -> None:
    """素材无可评分文本 → 0.0（不选为最优）。"""
    assert _heuristic_title_match_score("", [], "城市夜景") == 0.0


@pytest.mark.asyncio
async def test_validate_no_url_returns_zero() -> None:
    """无可访问 URL 的素材 → 0.0（无法校验，不作为最优候选）。"""
    asset = MaterialAsset(id="a1", title="城市夜景", tags=["城市"], url=None, local_path=None)
    with patch("clipwright.tool.registry.ToolRegistry.get", return_value=None):
        assert await _validate_video_frame(asset, "城市 夜景") == 0.0


@pytest.mark.asyncio
async def test_validate_matching_asset_scores_high() -> None:
    """启发式：有 URL 且标题/标签匹配 → 分数高于阈值。"""
    result = _asset("a1", "城市夜景 车流", ["城市", "夜景"])
    with patch("clipwright.tool.registry.ToolRegistry.get", return_value=None):
        score = await _validate_video_frame(result.asset, "城市夜景 城市 夜景")
    assert score > 0.5


# ── gate 关闭：视觉工具不被调用 ──


@pytest.mark.asyncio
async def test_gate_off_does_not_call_vision() -> None:
    """enable_visual_llm=False 时不调用任何视觉 LLM 工具。"""
    agent = MaterialAgent()
    results = [_asset("a1", "城市夜景 车流", ["城市", "夜景"])]
    with (
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(return_value=results),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(),
        ) as vision_execute,
    ):
        out = await _run_scene(agent, SCENE)

    vision_execute.assert_not_awaited()
    assert out["retried"] is False
    assert out["suggested_assets"][0]["asset_id"] == "a1"
    assert out["suggested_assets"][0]["score"] > 0.35


# ── 有界重试：全部失败时最多 2 次 ──


@pytest.mark.asyncio
async def test_retry_cap_two_retries() -> None:
    """校验恒为 0.0 → 重试循环恰好 2 次，_llm_search_queries 共调用 2 次。"""
    agent = MaterialAgent()
    bad = [_asset("bad-1", "无关素材", ["x"]), _asset("bad-2", "无关素材", ["x"])]
    queries_mock = AsyncMock(return_value=["retry-query"])
    with (
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(return_value=bad),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(return_value=0.0),
        ),
        patch("clipwright.agents.material_agent._llm_search_queries", new=queries_mock),
    ):
        out = await _run_scene(agent, SCENE, batch_query=None)

    assert queries_mock.await_count == 2
    assert out["retried"] is True
    assert out["validation_note"].startswith("retry_2")


# ── 换素材路径 ──


@pytest.mark.asyncio
async def test_swap_material_picks_second_valid() -> None:
    """首轮全部失败 → 换素材校验第 9 个候选并通过。"""
    agent = MaterialAgent()
    results = [_asset(f"bad-{i}", "无关素材", ["x"]) for i in range(8)]
    results.append(_asset("good-9", "城市夜景 车流", ["城市", "夜景"]))

    def _score(r, expected_text: str) -> float:
        return 0.8 if r.asset.id == "good-9" else 0.0

    with (
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(return_value=results),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(side_effect=_score),
        ),
    ):
        out = await _run_scene(agent, SCENE)

    assert out["retried"] is True
    assert out["validation_note"].startswith("retry_1")
    assert out["suggested_assets"][0]["asset_id"] == "good-9"
    assert out["score"] >= 0.35


# ── 换搜索词路径 ──


@pytest.mark.asyncio
async def test_requery_with_retry_hint() -> None:
    """全部失败 → 第 2 次重试重新生成搜索词（retry_hint=True）并命中新素材。"""
    agent = MaterialAgent()
    bad = [_asset("bad-1", "无关素材", ["x"])]

    def _search_side(query: str, top_k: int = 5, source_ids=None):
        if "retry" in query:
            return [_asset("good-2", "城市夜景 车流", ["城市", "夜景"])]
        return bad

    def _score(r, expected_text: str) -> float:
        return 0.8 if r.asset.id == "good-2" else 0.0

    queries_mock = AsyncMock(
        side_effect=lambda *a, **k: ["retry-query"] if k.get("retry_hint") else ["q1"]
    )

    with (
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(side_effect=_search_side),
        ),
        patch(
            "clipwright.agents.material_agent._validate_video_frame",
            new=AsyncMock(side_effect=_score),
        ),
        patch("clipwright.agents.material_agent._llm_search_queries", new=queries_mock),
    ):
        out = await _run_scene(agent, SCENE, batch_query=None)

    assert queries_mock.await_count == 2
    assert queries_mock.await_args.kwargs.get("retry_hint") is True
    assert out["retried"] is True
    assert out["suggested_assets"][0]["asset_id"] == "good-2"
    assert out["score"] >= 0.35


# ── execute 集成 ──


@pytest.mark.asyncio
async def test_execute_integration_gate_off() -> None:
    """execute 全链路：gate 关闭 + 匹配素材 → 高分选中，视觉工具不被调用。"""
    context = _context()
    input_data = MaterialInput(
        context=context,
        script_skeleton={"scenes": [SCENE]},
        material_plugin_config={"enable_visual_llm": False},
    )
    results = [_asset("a1", "城市夜景 车流", ["城市", "夜景"])]
    with (
        patch(
            "clipwright.agents.material_agent.MaterialRegistry.list",
            return_value=[{"id": "src"}],
        ),
        patch(
            "clipwright.agents.material_agent._search_with_cache",
            new=AsyncMock(return_value=results),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries_batch",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "clipwright.agents.material_agent._llm_search_queries",
            new=AsyncMock(return_value=["q1"]),
        ),
        patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(),
        ) as vision_execute,
    ):
        output = await MaterialAgent().execute(input_data, context)

    assert output.decision == AgentDecision.PASS
    assert output.candidate_clips[0]["suggested_assets"][0]["score"] > 0.35
    assert output.candidate_clips[0]["retried"] is False
    vision_execute.assert_not_awaited()
