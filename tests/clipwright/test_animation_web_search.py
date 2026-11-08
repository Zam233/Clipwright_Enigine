"""W5 — 动画 Agent 联网搜索门控测试（mg_dynamic 数据/事实型）。

**接入方式在 2026-11-02 变过一次**：旧实现直连 `WebSearchService.search()`（agent 里
`import` 具体服务）；现在按不变量 #19 收口到**能力面**：

- `fact_need`（插件 `web_media` 提供）——模型判断"哪些场景需要外部事实"，一次批量调用；
- `web_search` / `web_fetch`（工具/插件提供）——真正取资料。

所以本文件的打桩点从 `animation_agent.WebSearchService` 换成了 `AnimationAgent.call_tool`，
断言语义保持不变：事实型→搜且注入上下文；非事实型→不搜；能力缺失/报错→空上下文但不炸。

覆盖：
(a) 事实型场景 + 能力可用 → `web_search` 被调用，`mg_gen` 收到含标题/摘要/来源的 web_context；
(b) 没有事实型场景 → 不调用搜索，web_context == ""；
(c) `fact_need` 能力缺失 → 不调用搜索，生成照常（行为与接入前一致）；
(d) 搜索能力不可用 / 抛异常 → web_context == ""，异常不传播。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AnimationInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.schema.tool import ToolExecResult, ToolStatus

_FAKE_RESULTS = [
    {"title": "2025 市场报告", "url": "https://example.com/market-2025",
     "snippet": "2025 年市场规模增长 23%"},
    {"title": "行业统计", "url": "https://example.com/stats",
     "snippet": "行业统计显示市占率 45%"},
]


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
    """MGGenerator 桩：`generate` / `generate_for_type` 都记录（含 web_context）。"""

    _RESULT = {
        "success": True,
        "html": "<div class='mg-anim'>W5</div>",
        "mg_def": {"animation_id": "mg_web_search", "duration_sec": 3.0},
        "method": "llm",
        "fallback_template": None,
        "generation_id": "gen_w5_test",
    }

    def __init__(self) -> None:
        self.generate = AsyncMock(return_value=dict(self._RESULT))
        self.generate_for_type = AsyncMock(return_value=dict(self._RESULT))

    def __call__(self) -> "_FakeMGGenerator":
        return self

    def _calls(self) -> list:
        return list(self.generate.await_args_list) + list(self.generate_for_type.await_args_list)

    def last_web_context(self) -> str:
        calls = self._calls()
        assert calls, "MG 生成未被调用"
        return calls[-1].kwargs.get("web_context", "")


class _FakeCapabilities:
    """能力面桩：`fact_need` / `web_search` / `web_fetch` 三个能力。

    - `fact_need`：按 `needed_ids` 配置返回（空 = 没场景需要事实）；
      `fact_need_missing=True` 模拟插件未装（返回 NOT_FOUND）。
    - `web_search`：记录查询；`fail=True` 抛异常；`search_available=False` 返回
      DEPENDENCY_MISSING（等价于"搜索未配置"）。
    """

    def __init__(
        self,
        *,
        needed_ids: list[str] | None = None,
        results: list[dict] | None = None,
        fail: bool = False,
        fact_need_missing: bool = False,
        search_available: bool = True,
    ) -> None:
        self.needed_ids = list(needed_ids or [])
        self.results = results if results is not None else list(_FAKE_RESULTS)
        self.fail = fail
        self.fact_need_missing = fact_need_missing
        self.search_available = search_available
        self.search_calls: list[dict[str, Any]] = []
        self.fetch_calls: list[dict[str, Any]] = []

    async def __call__(self, capability: str, **kwargs: Any) -> ToolExecResult:
        if capability == "fact_need":
            if self.fact_need_missing:
                return ToolExecResult(
                    status=ToolStatus.NOT_FOUND, tool_name="fact_need",
                    output={}, error="能力缺失",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name="fact_need",
                output={"needed_ids": self.needed_ids, "mode": "test"},
            )
        if capability == "web_search":
            self.search_calls.append(dict(kwargs))
            if self.fail:
                raise RuntimeError("web search boom")
            if not self.search_available:
                return ToolExecResult(
                    status=ToolStatus.DEPENDENCY_MISSING, tool_name="web_search",
                    output={"results": []}, error="搜索未配置",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name="web_search",
                output={"results": list(self.results)},
            )
        if capability == "web_fetch":
            self.fetch_calls.append(dict(kwargs))
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name="web_fetch",
                output={"content": "", "fetched": True},
            )
        return ToolExecResult(
            status=ToolStatus.NOT_FOUND, tool_name=capability, output={},
            error="未知能力",
        )


async def _run_execute(timeline: Timeline, caps: _FakeCapabilities, fake_mg: _FakeMGGenerator):
    """以标准桩组合运行 AnimationAgent.execute（patch 必须在 await 期间保持生效）。"""
    agent = AnimationAgent()
    ctx = _ctx()
    inp = AnimationInput(context=ctx, timeline=timeline)
    with (
        patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
        patch.object(AnimationAgent, "call_tool", new=caps),
        patch("clipwright.animation.mg.MGGenerator", fake_mg),
    ):
        return await agent.execute(inp, ctx)


class TestDataFactGatedSearch:
    """事实型场景 → 搜索 + web_context 注入。"""

    async def test_data_description_triggers_search_and_web_context(self) -> None:
        caps = _FakeCapabilities(needed_ids=["clip_mg"])
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("2025 年市场数据展示"), caps, fake_mg)

        # (a) 搜索被调用，查询 = 描述前 120 字符
        assert len(caps.search_calls) == 1, caps.search_calls
        assert caps.search_calls[0]["query"] == "2025 年市场数据展示"

        # 生成侧收到真实 web_context 文本（含摘要/标题/来源）
        web_context = fake_mg.last_web_context()
        assert web_context
        assert "2025 年市场规模增长 23%" in web_context
        assert "2025 市场报告" in web_context
        assert "https://example.com/market-2025" in web_context
        assert "行业统计显示市占率 45%" in web_context
        assert "https://example.com/stats" in web_context

        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    async def test_no_fact_scene_skips_search(self) -> None:
        """`fact_need` 判定没有场景需要事实 → 不调用搜索，web_context == ""。"""
        caps = _FakeCapabilities(needed_ids=[])
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("标题揭示动画"), caps, fake_mg)

        assert caps.search_calls == []
        assert fake_mg.last_web_context() == ""
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    async def test_fact_need_capability_missing_falls_back(self) -> None:
        """`fact_need` 能力缺失（插件未装）→ 不做事实判断、不搜索，生成照常。"""
        caps = _FakeCapabilities(fact_need_missing=True)
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("2025 年市场数据"), caps, fake_mg)

        assert caps.search_calls == []
        assert fake_mg.last_web_context() == ""
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    async def test_search_capability_missing_keeps_empty_context(self) -> None:
        """搜索能力不可用（未配置）→ 空上下文，生成照常（行为与接入前一致）。"""
        caps = _FakeCapabilities(needed_ids=["clip_mg"], search_available=False)
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("2025 年市场数据展示"), caps, fake_mg)

        assert len(caps.search_calls) == 1
        assert fake_mg.last_web_context() == ""
        assert out.decision == AgentDecision.PASS

    async def test_search_exception_swallowed(self) -> None:
        """搜索抛异常 → web_context == ""，异常不传播，生成照常。"""
        caps = _FakeCapabilities(needed_ids=["clip_mg"], fail=True)
        fake_mg = _FakeMGGenerator()
        out = await _run_execute(_timeline_with_mg_dynamic("2025 年市场数据展示"), caps, fake_mg)

        assert len(caps.search_calls) == 1
        assert fake_mg.last_web_context() == ""
        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1
