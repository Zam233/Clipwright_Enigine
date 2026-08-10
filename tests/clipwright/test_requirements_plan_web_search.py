"""W2: 规划书生成注入联网搜索结果（web_context）门控测试。

覆盖：
1. 已配置 + 搜索结果非空 → StructureAgent 收到的 rag_context 含「联网搜索参考」段落
2. 未配置 → rag_context 与 _retrieve_knowledge 返回逐字节一致（零变化）
3. 已配置 + 搜索返回空列表 → 无联网段落
4. translate_scenes web_context 非空/空 → system_prompt 含/不含联网段落
5. 搜索抛异常 → "" 且不崩溃（零变化）
另含 revision（B6/E2）路径注入 + _build_web_context 直接单测。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.agents.requirements_agent import RequirementsAgent
from clipwright.config import settings
from clipwright.services.requirements_service import (
    RequirementsService,
    _build_web_context,
)


def _make_service() -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._llm = AsyncMock()
    svc._cleanup_started = True
    return svc


def _configure_web(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """开关 WebSearchService 配置（is_configured = enable_web_search and api_key）。"""
    monkeypatch.setattr(settings, "enable_web_search", enabled)
    monkeypatch.setattr(settings, "web_search_api_key", "test-key" if enabled else "")


def _search_results() -> list[dict[str, Any]]:
    return [
        {"title": "R1", "url": "https://a.com", "snippet": "snippet-one", "score": 0.9},
        {"title": "R2", "url": "https://b.com", "snippet": "snippet-two", "score": 0.8},
    ]


def _structure_instance(scenes: list[dict] | None = None) -> MagicMock:
    """StructureAgent 实例 mock：execute 捕获输入并返回含场景的结果。"""
    inst = MagicMock()
    inst.execute = AsyncMock(
        return_value=SimpleNamespace(
            scenes=scenes or [{"title": "s1", "duration_sec": 60.0}]
        )
    )
    return inst


def _plan() -> dict:
    return {"markdown_content": "PLAN", "scene_count": 1, "total_duration_sec": 60, "raw_scenes": []}


def _user_inputs(**overrides: Any) -> dict:
    data = {
        "topic": "主题",
        "persona_id": "default",
        "category_plugin_id": "knowledge_longform",
        "script_text": "文稿",
    }
    data.update(overrides)
    return data


class TestBuildWebContext:
    async def test_configured_formats_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已配置 + 结果 → 拼接「标题|摘要|来源」段落。"""
        _configure_web(monkeypatch, enabled=True)
        with patch(
            "clipwright.services.web_search.WebSearchService.search",
            new=AsyncMock(return_value=_search_results()),
        ) as mock_search:
            out = await _build_web_context("主题 文稿", max_results=3)

        mock_search.assert_awaited_once()
        assert "snippet-one" in out
        assert "R1" in out
        assert "https://a.com" in out
        assert "snippet-two" in out

    async def test_not_configured_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置 → 不触发搜索，返回 ""。"""
        _configure_web(monkeypatch, enabled=False)
        with patch(
            "clipwright.services.web_search.WebSearchService.search",
            new=AsyncMock(),
        ) as mock_search:
            out = await _build_web_context("主题 文稿")

        assert out == ""
        mock_search.assert_not_awaited()

    async def test_empty_results_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + 空结果 → "". """
        _configure_web(monkeypatch, enabled=True)
        with patch(
            "clipwright.services.web_search.WebSearchService.search",
            new=AsyncMock(return_value=[]),
        ):
            assert await _build_web_context("主题 文稿") == ""

    async def test_search_raises_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """搜索抛异常 → 不崩溃，返回 ""。"""
        _configure_web(monkeypatch, enabled=True)
        with patch(
            "clipwright.services.web_search.WebSearchService.search",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            assert await _build_web_context("主题 文稿") == ""


class TestGeneratePlanInjection:
    async def test_configured_rag_context_contains_web_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + 结果 → StructureAgent 的 rag_context 含搜索结果片段。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        inst = _structure_instance()
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(return_value=_search_results()),
            ),
            patch("clipwright.agents.structure_agent.StructureAgent", return_value=inst),
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="KNOWLEDGE")),
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())) as mock_tr,
        ):
            result = await svc._generate_plan(
                {"title": "主题"}, _user_inputs(), "req_w2_1",
            )

        input_data = inst.execute.call_args[0][0]
        rag = input_data.rag_context
        assert "KNOWLEDGE" in rag
        assert "## 联网搜索参考" in rag
        assert "snippet-one" in rag
        assert "R1" in rag
        assert "https://a.com" in rag
        assert result == _plan()
        # 翻译步骤同样收到 web_context
        assert mock_tr.call_args.kwargs.get("web_context")
        assert "snippet-one" in mock_tr.call_args.kwargs["web_context"]

    async def test_configured_web_only_when_no_knowledge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + 知识库为空 → rag_context 仍为联网段落（非零变化分支）。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        inst = _structure_instance()
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(return_value=_search_results()),
            ),
            patch("clipwright.agents.structure_agent.StructureAgent", return_value=inst),
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())),
        ):
            await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_w2_2")

        rag = inst.execute.call_args[0][0].rag_context
        assert rag.startswith("## 联网搜索参考\n")
        assert "snippet-one" in rag

    async def test_not_configured_rag_context_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置 → rag_context 与 _retrieve_knowledge 返回完全一致（零变化）。"""
        _configure_web(monkeypatch, enabled=False)
        svc = _make_service()
        inst = _structure_instance()
        knowledge = "知识库内容 \n- 条目一\n- 条目二"
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(),
            ) as mock_search,
            patch("clipwright.agents.structure_agent.StructureAgent", return_value=inst),
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value=knowledge)),
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())) as mock_tr,
        ):
            await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_w2_3")

        rag = inst.execute.call_args[0][0].rag_context
        assert rag == knowledge
        assert "联网搜索参考" not in rag
        mock_search.assert_not_awaited()
        # 未配置 → 翻译步骤不注入 web_context
        assert mock_tr.call_args.kwargs.get("web_context", "") == ""

    async def test_configured_empty_search_no_web_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + 搜索空结果 → rag_context 无联网段落。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        inst = _structure_instance()
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(return_value=[]),
            ),
            patch("clipwright.agents.structure_agent.StructureAgent", return_value=inst),
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="KNOWLEDGE")),
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())),
        ):
            await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_w2_4")

        assert inst.execute.call_args[0][0].rag_context == "KNOWLEDGE"

    async def test_configured_search_raises_no_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + 搜索抛异常 → 不崩溃，rag_context 零变化。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        inst = _structure_instance()
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("clipwright.agents.structure_agent.StructureAgent", return_value=inst),
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="KNOWLEDGE")),
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())),
        ):
            result = await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_w2_5")

        assert inst.execute.call_args[0][0].rag_context == "KNOWLEDGE"
        assert result == _plan()

    async def test_revision_path_passes_web_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B6/E2 修订路径：复用 raw_scenes 时同样注入 web_context 到 _translate_plan。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        scenes = [{"title": "s1", "duration_sec": 60.0}]
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(return_value=_search_results()),
            ),
            patch("clipwright.agents.structure_agent.StructureAgent") as mock_cls,
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())) as mock_tr,
        ):
            result = await svc._generate_plan(
                {"title": "主题"}, _user_inputs(), "req_w2_6",
                feedback="改一下", existing_raw_scenes=scenes,
            )

        mock_cls.assert_not_called()
        assert result == _plan()
        assert mock_tr.call_args.kwargs.get("web_context")
        assert "snippet-one" in mock_tr.call_args.kwargs["web_context"]


class _CapturingLLM:
    """记录 system_prompt，返回合法规划书 dict。"""

    def __init__(self) -> None:
        self.system_prompt = ""

    async def structured_output(self, **kwargs: Any) -> dict[str, Any]:
        self.system_prompt = kwargs.get("system_prompt", "")
        return {
            "summary": "s",
            "markdown_content": "m",
            "total_duration_sec": 60,
            "scene_count": 1,
        }


class TestTranslateScenes:
    def _agent(self) -> RequirementsAgent:
        agent = RequirementsAgent.__new__(RequirementsAgent)
        agent._llm = _CapturingLLM()
        return agent

    async def test_web_context_injects_paragraph(self) -> None:
        """web_context 非空 → system_prompt 含「联网搜索参考」。"""
        agent = self._agent()
        await agent.translate_scenes(
            [{"title": "s1"}], brief={"title": "t"}, web_context="WEB SNIPPET"
        )
        assert "## 联网搜索参考" in agent._llm.system_prompt
        assert "WEB SNIPPET" in agent._llm.system_prompt

    async def test_empty_web_context_no_paragraph(self) -> None:
        """web_context 为空（默认）→ system_prompt 与现状一致。"""
        agent = self._agent()
        await agent.translate_scenes([{"title": "s1"}], brief={"title": "t"})
        assert "联网搜索参考" not in agent._llm.system_prompt
