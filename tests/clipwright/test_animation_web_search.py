"""W5 — 动画 Agent 联网搜索门控测试（mg_dynamic 数据/事实型，见 docs/agent-search-cancel.md）。

覆盖：
(a) 描述含数据/事实关键词（如 "2025 年市场数据"）+ 搜索已配置 → WebSearchService.search
    被调用，且 mg_gen.generate 收到 web_context（含搜索结果标题/摘要/来源）
(b) 描述为纯标题揭示（无数据/事实关键词）→ 搜索不调用，web_context == ""
(c) 未配置（settings.enable_web_search=False）→ 搜索不调用，generate 照常（行为不变）
(d) 搜索抛异常 → web_context == ""，异常不传播（绝不 raise）

对抗类：
- misleading_success_output：断言 generate 收到真实 web_context 文本（含搜索摘要）
- 未配置/失败分支：断言 web_context == ""（行为与接入前逐字节一致）

注：pyproject 已启用 pytest-asyncio asyncio_mode="auto"，async def test_ 直接执行。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.config import settings
from clipwright.schema.agent import AgentContext, AgentDecision, AnimationInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.web_search import WebSearchService


def _ctx() -> AgentContext:
    return AgentContext(
        pipeline_id="p_w5_web",
        persona_id="persona_w5",
        category_plugin_id="cat_w5",
        topic="w5 web search",
        extra_params={},
    )


def _timeline_with_mg_dynamic(description: str) -> Timeline:
    """构造含 mg_dynamic 标记的时间线；description 作为 payload 描述注入标记。"""
    import json as _json

    payload = _json.dumps(
        {"description": description, "text": "数据|图表", "style": "tech_dark"},
        ensure_ascii=False,
    )
    tl = Timeline()
    tl.tracks.append(
        Track(
            id="v_main",
            name="视频轨",
            kind=ClipKind.VIDEO,
            index=0,
            clips=[
                Clip(
                    id="clip_mg",
                    kind=ClipKind.VIDEO,
                    asset_id="a1",
                    track_id="v_main",
                    start_sec=0.0,
                    duration_sec=5.0,
                    metadata={"description": f"科技内容 [逻辑动画]mg_dynamic:{payload}"},
                ),
            ],
        )
    )
    return tl


class _FakeMGGenerator:
    """MGGenerator 桩：记录 generate 调用（含 web_context），返回成功结果。"""

    def __init__(self) -> None:
        self.generate = AsyncMock(
            return_value={
                "success": True,
                "html": "<div class='mg-anim'>W5</div>",
                "mg_def": {"animation_id": "mg_web_search", "duration_sec": 3.0},
                "method": "llm",
                "fallback_template": None,
                "generation_id": "gen_w5_test",
            }
        )

    def __call__(self) -> "_FakeMGGenerator":
        return self

    def last_web_context(self) -> str:
        calls = self.generate.await_args_list
        assert calls, "generate 未被调用"
        return calls[-1].kwargs.get("web_context", "")

    def last_description(self) -> str:
        calls = self.generate.await_args_list
        assert calls, "generate 未被调用"
        return calls[-1].kwargs.get("description", "")


class _FakeWebSearch:
    """WebSearchService 桩：is_configured / search 可配置；记录搜索调用。"""

    def __init__(self, configured: bool = True, fail: bool = False,
                 results: list[dict] | None = None) -> None:
        self.configured = configured
        self.fail = fail
        self.results = results or []
        self.instances = 0
        self.search_calls = 0
        self.search_queries: list[str] = []

    def __call__(self) -> "_FakeWebSearch":
        self.instances += 1
        return self

    def is_configured(self) -> bool:
        return self.configured

    async def search(self, query: str, max_results: int | None = None) -> list[dict]:
        self.search_calls += 1
        self.search_queries.append(query)
        if self.fail:
            raise RuntimeError("web search boom")
        return self.results


_FAKE_RESULTS = [
    {"title": "2025 市场报告", "url": "https://example.com/market-2025",
     "snippet": "2025 年市场规模增长 23%"},
    {"title": "行业统计", "url": "https://example.com/stats",
     "snippet": "行业统计显示市占率 45%"},
]


async def _run_execute(timeline: Timeline, fake_ws: _FakeWebSearch, fake_mg: _FakeMGGenerator):
    """以标准桩组合运行 AnimationAgent.execute（patch 必须在 await 期间保持生效）。"""
    agent = AnimationAgent()
    ctx = _ctx()
    inp = AnimationInput(context=ctx, timeline=timeline)
    with (
        patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
        patch("clipwright.agents.animation_agent.WebSearchService", fake_ws),
        patch("clipwright.animation.mg.MGGenerator", fake_mg),
    ):
        return await agent.execute(inp, ctx)


class TestDataFactGatedSearch:
    """数据/事实型描述 → 搜索 + web_context 注入。"""

    async def test_data_description_triggers_search_and_web_context(self) -> None:
        fake_ws = _FakeWebSearch(configured=True, results=_FAKE_RESULTS)
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("2025 年市场数据展示"), fake_ws, fake_mg)

        # (a) 搜索被调用且查询 = 描述前 120 字符
        assert fake_ws.search_calls == 1
        assert fake_ws.search_queries == ["2025 年市场数据展示"]

        # generate 收到真实 web_context 文本（含摘要/标题/来源，≤3 条）
        web_context = fake_mg.last_web_context()
        assert web_context
        assert "2025 年市场规模增长 23%" in web_context
        assert "2025 市场报告" in web_context
        assert "https://example.com/market-2025" in web_context
        assert "行业统计显示市占率 45%" in web_context
        assert "https://example.com/stats" in web_context

        # 原始描述保留（无回归）
        assert fake_mg.last_description() == "2025 年市场数据展示"
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    async def test_no_data_keywords_skips_search(self) -> None:
        """纯标题揭示（无数据/事实关键词）→ 搜索不调用，web_context == ""。"""
        fake_ws = _FakeWebSearch(configured=True, results=_FAKE_RESULTS)
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("标题揭示动画"), fake_ws, fake_mg)

        assert fake_ws.search_calls == 0
        assert fake_mg.last_web_context() == ""
        # 行为不变：generate 照常调用并生成 clip
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    async def test_not_configured_skips_search(self, monkeypatch) -> None:
        """未配置（settings.enable_web_search=False）→ 搜索不调用，行为与之前完全一致。"""
        monkeypatch.setattr(settings, "enable_web_search", False)
        monkeypatch.setattr(settings, "web_search_api_key", "")
        fake_mg = _FakeMGGenerator()
        agent = AnimationAgent()
        ctx = _ctx()
        inp = AnimationInput(context=ctx, timeline=_timeline_with_mg_dynamic("2025 年市场数据"))
        with (
            patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
            # 真实 WebSearchService：is_configured 读取被 monkeypatch 的 settings
            patch.object(WebSearchService, "search", new=AsyncMock()) as mock_search,
            patch("clipwright.animation.mg.MGGenerator", fake_mg),
        ):
            out = await agent.execute(inp, ctx)

        mock_search.assert_not_called()
        assert fake_mg.last_web_context() == ""
        # 行为不变：generate 照常调用
        assert fake_mg.generate.await_count == 1
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    async def test_search_exception_swallowed(self) -> None:
        """搜索抛异常 → web_context == ""，异常不传播，generate 照常。"""
        fake_ws = _FakeWebSearch(configured=True, fail=True)
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("2025 年市场数据展示"), fake_ws, fake_mg)

        assert fake_ws.search_calls == 1
        assert fake_mg.last_web_context() == ""
        assert fake_mg.generate.await_count == 1
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1
