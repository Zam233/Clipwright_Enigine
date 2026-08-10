"""A2: 素材库概览注入需求简报（_generate_brief）与规划书（_generate_plan）门控测试。

覆盖：
1. 注册 2 个素材源 → _material_library_overview() 返回含两个源名的概览
2. 空注册表（list → []）→ helper 返回 ""
3. _generate_brief 有素材源 → LLM user_prompt 含「素材库概览」
4. _generate_plan 有素材源 → StructureAgent 的 rag_context 含「素材库概览」
5. 空注册表 → 简报 user_prompt 与规划 rag_context 均不含「素材库概览」（零变化）

隔离方式：仅 monkeypatch MaterialRegistry.list（不触碰 class-level _sources 单例状态），
monkeypatch 自动恢复，不影响其他测试模块的已注册素材源。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.agents.requirements_agent import RequirementsAgent
from clipwright.material.registry import MaterialRegistry
from clipwright.schema.agent import AgentContext, RequirementsInput
from clipwright.services.requirements_service import (
    RequirementsService,
    _material_library_overview,
)


def _two_sources() -> list[dict[str, str]]:
    return [{"id": "a", "name": "源A"}, {"id": "b", "name": "源B"}]


def _mock_registry(monkeypatch: pytest.MonkeyPatch, sources: list[dict]) -> None:
    """monkeypatch MaterialRegistry.list（classmethod → staticmethod）。"""
    monkeypatch.setattr(MaterialRegistry, "list", staticmethod(lambda: sources))


def _make_service() -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._llm = AsyncMock()
    svc._cleanup_started = True
    return svc


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
    return {
        "markdown_content": "PLAN", "scene_count": 1,
        "total_duration_sec": 60, "raw_scenes": [],
    }


def _user_inputs(**overrides: Any) -> dict:
    data = {
        "topic": "主题",
        "persona_id": "default",
        "category_plugin_id": "knowledge_longform",
        "script_text": "文稿",
    }
    data.update(overrides)
    return data


def _brief_context() -> AgentContext:
    return AgentContext(
        pipeline_id="req_a2_brief",
        persona_id="default",
        category_plugin_id="knowledge_longform",
        topic="科技",
    )


class _CapturingBriefLLM:
    """记录 user_prompt，返回含 brief_draft 的合法结果。"""

    def __init__(self) -> None:
        self.user_prompt = ""

    async def structured_output(self, **kwargs: Any) -> dict[str, Any]:
        self.user_prompt = kwargs.get("user_prompt", "")
        return {"brief_draft": {"title": "标题", "overview": "概述"}, "is_ready": True}


def _brief_agent() -> RequirementsAgent:
    agent = RequirementsAgent.__new__(RequirementsAgent)
    agent._llm = _CapturingBriefLLM()
    return agent


class TestMaterialLibraryOverview:
    def test_helper_lists_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """注册 2 个源 → 概览字符串包含两个源名。"""
        _mock_registry(monkeypatch, _two_sources())
        out = _material_library_overview()
        assert "源A" in out
        assert "源B" in out
        assert "2 个素材源" in out

    def test_helper_empty_registry_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空注册表 → ""。"""
        _mock_registry(monkeypatch, [])
        assert _material_library_overview() == ""

    def test_helper_registry_raises_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """注册表异常 → ""（零变化兜底）。"""
        def _boom() -> list[dict]:
            raise RuntimeError("registry down")

        monkeypatch.setattr(MaterialRegistry, "list", staticmethod(_boom))
        assert _material_library_overview() == ""


class TestGenerateBriefInjection:
    async def test_brief_injects_overview_with_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有素材源 → _generate_brief 的 LLM user_prompt 含「素材库概览」。"""
        _mock_registry(monkeypatch, _two_sources())
        agent = _brief_agent()
        context = _brief_context()
        input_data = RequirementsInput(context=context, topic="科技", script_text="文稿")
        await agent._generate_brief(input_data, context)
        assert "素材库概览" in agent._llm.user_prompt
        assert "源A" in agent._llm.user_prompt
        assert "源B" in agent._llm.user_prompt

    async def test_brief_no_overview_when_empty_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空素材库 → 简报 user_prompt 不含「素材库概览」（零变化）。"""
        _mock_registry(monkeypatch, [])
        agent = _brief_agent()
        context = _brief_context()
        input_data = RequirementsInput(context=context, topic="科技", script_text="文稿")
        await agent._generate_brief(input_data, context)
        assert "素材库概览" not in agent._llm.user_prompt
        assert agent._llm.user_prompt == "选题: 科技\n文稿预览: 文稿\n"


class TestGeneratePlanInjection:
    async def test_plan_injects_overview_with_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有素材源 → StructureAgent 的 rag_context 含「素材库概览」。"""
        _mock_registry(monkeypatch, _two_sources())
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
            await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_a2_plan")

        rag = inst.execute.call_args[0][0].rag_context
        assert "## 素材库概览" in rag
        assert "源A" in rag
        assert "源B" in rag
        assert rag.startswith("KNOWLEDGE")

    async def test_plan_overview_only_when_no_knowledge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """知识库为空 + 有素材源 → rag_context 仍以素材库概览开头（非零变化分支）。"""
        _mock_registry(monkeypatch, _two_sources())
        svc = _make_service()
        inst = _structure_instance()
        with (
            patch(
                "clipwright.services.web_search.WebSearchService.search",
                new=AsyncMock(return_value=[]),
            ),
            patch("clipwright.agents.structure_agent.StructureAgent", return_value=inst),
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch.object(svc, "_translate_plan", new=AsyncMock(return_value=_plan())),
        ):
            await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_a2_plan2")

        rag = inst.execute.call_args[0][0].rag_context
        assert rag.startswith("## 素材库概览\n")
        assert "源A" in rag

    async def test_plan_no_overview_when_empty_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空素材库 → 规划 rag_context 不含「素材库概览」（零变化）。"""
        _mock_registry(monkeypatch, [])
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
            await svc._generate_plan({"title": "主题"}, _user_inputs(), "req_a2_plan3")

        rag = inst.execute.call_args[0][0].rag_context
        assert "素材库概览" not in rag
        assert rag == "KNOWLEDGE"
