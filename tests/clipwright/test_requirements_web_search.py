"""W1: 需求对话 LLM 轮次接入联网搜索工具（web_search / web_fetch）门控测试。

覆盖：
1. 未配置 → 走原 llm_call_with_retry 路径（与现状字节一致），with_tools 不被调用
2. 已配置 + with_tools 返回有效 JSON → 解析返回（brief_draft 存在）
3. 已配置 + with_tools 返回非 JSON → 优雅降级 dict（reply + is_ready False）
4. 已配置 + with_tools 抛异常 → 回退原路径，异常不外泄
5. _web_tool_executor 经 ToolRegistry 分发（成功 / 失败空结果）
另含一条 chat() 级集成测试（gathering → brief_ready）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.config import settings
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services.requirements_service import (
    RequirementsService,
    _web_tool_executor,
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


def _ok_response() -> dict:
    return {
        "reply": "这是方案摘要",
        "brief_draft": {"title": "测试标题", "overview": "概述"},
        "is_ready": True,
        "missing_info": [],
    }


def _fake_tool(name: str = "web_search", description: str = "联网搜索") -> SimpleNamespace:
    """构造与 BaseTool 兼容的最小假工具（仅暴露 with_tools 构建 schema 所需字段）。"""
    return SimpleNamespace(
        name=name,
        description=description,
        to_llm_tool=lambda fmt: {
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}},
            }
        },
    )


def _handle_gathering(svc: RequirementsService) -> Any:
    return svc._handle_gathering(
        [{"role": "user", "content": "帮我做个科技视频"}],
        None,
        "gathering",
        {"topic": "科技"},
        "req_w1_session",
    )


class TestNotConfigured:
    async def test_gathering_uses_original_path_when_web_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置 → 原 llm_call_with_retry 路径被调用，with_tools 不被调用。"""
        _configure_web(monkeypatch, enabled=False)
        svc = _make_service()
        expected = _ok_response()
        with (
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch(
                "clipwright.services.requirements_service.llm_call_with_retry",
                new=AsyncMock(return_value=expected),
            ) as mock_retry,
        ):
            result = await _handle_gathering(svc)

        assert result == expected
        assert result.get("brief_draft", {}).get("title") == "测试标题"
        mock_retry.assert_awaited_once()
        svc._llm.with_tools.assert_not_called()


class TestConfigured:
    async def test_gathering_parses_json_from_with_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + with_tools 返回有效 JSON → 直接解析返回。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        svc._llm.with_tools.return_value = SimpleNamespace(
            content=json.dumps(_ok_response(), ensure_ascii=False)
        )
        with (
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch(
                "clipwright.tool.registry.ToolRegistry.list_agent_callable",
                return_value=[_fake_tool()],
            ),
        ):
            result = await _handle_gathering(svc)

        assert result.get("brief_draft", {}).get("title") == "测试标题"
        assert result.get("is_ready") is True
        svc._llm.with_tools.assert_awaited_once()

    async def test_gathering_graceful_when_with_tools_returns_non_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + with_tools 返回非 JSON 文本 → 优雅降级 dict。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        svc._llm.with_tools.return_value = SimpleNamespace(content="好的，请继续描述你的想法。")
        with (
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch(
                "clipwright.tool.registry.ToolRegistry.list_agent_callable",
                return_value=[_fake_tool()],
            ),
        ):
            result = await _handle_gathering(svc)

        assert result == {
            "reply": "好的，请继续描述你的想法。",
            "brief_draft": {},
            "is_ready": False,
        }
        svc._llm.with_tools.assert_awaited_once()

    async def test_gathering_graceful_when_with_tools_content_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + with_tools 返回空 content → 默认提示文本降级。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        svc._llm.with_tools.return_value = SimpleNamespace(content="")
        with (
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch(
                "clipwright.tool.registry.ToolRegistry.list_agent_callable",
                return_value=[_fake_tool()],
            ),
        ):
            result = await _handle_gathering(svc)

        assert result == {
            "reply": "请继续描述你的想法。",
            "brief_draft": {},
            "is_ready": False,
        }

    async def test_gathering_falls_back_when_with_tools_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置 + with_tools 抛异常 → 回退原 llm_call_with_retry 路径，异常不外泄。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        svc._llm.with_tools.side_effect = RuntimeError("with_tools boom")
        expected = _ok_response()
        with (
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch(
                "clipwright.tool.registry.ToolRegistry.list_agent_callable",
                return_value=[_fake_tool()],
            ),
            patch(
                "clipwright.services.requirements_service.llm_call_with_retry",
                new=AsyncMock(return_value=expected),
            ) as mock_retry,
        ):
            result = await _handle_gathering(svc)

        assert result == expected
        svc._llm.with_tools.assert_awaited_once()
        mock_retry.assert_awaited_once()


class TestWebToolExecutor:
    async def test_routes_through_tool_registry(self) -> None:
        """_web_tool_executor 经 ToolRegistry.execute 分发并返回 model_dump 结果。"""
        exec_result = ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name="web_search",
            output={"results": [{"title": "t", "url": "u", "snippet": "s"}]},
        )
        with patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(return_value=exec_result),
        ) as mock_execute:
            out = await _web_tool_executor("web_search", {"query": "科技"})

        mock_execute.assert_awaited_once_with("web_search", query="科技")
        assert out["status"] == "success"
        assert out["output"] == {"results": [{"title": "t", "url": "u", "snippet": "s"}]}

    async def test_returns_empty_results_on_failure(self) -> None:
        """工具执行异常 → web_search 返回空结果列表，绝不抛异常。"""
        with patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            out = await _web_tool_executor("web_search", {"query": "x"})
        assert out == {"results": []}

    async def test_returns_empty_content_on_fetch_failure(self) -> None:
        """工具执行异常 → web_fetch 返回空内容，绝不抛异常。"""
        with patch(
            "clipwright.tool.registry.ToolRegistry.execute",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            out = await _web_tool_executor("web_fetch", {"url": "https://x.com"})
        assert out == {"content": ""}


class TestChatIntegration:
    async def test_chat_gathering_with_web_configured_reaches_brief_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """chat() 全链路：已配置 + with_tools 返回 JSON → gathering 进入 brief_ready。"""
        _configure_web(monkeypatch, enabled=True)
        svc = _make_service()
        session_id = "req_w1_chat"
        from clipwright.services.requirements_service import _memory_sessions

        _memory_sessions[session_id] = {
            "session_id": session_id,
            "status": "gathering",
            "messages": [],
            "user_inputs": {"topic": "科技"},
            "creative_brief": None,
            "production_plan": None,
        }
        svc._llm.with_tools.return_value = SimpleNamespace(
            content=json.dumps(_ok_response(), ensure_ascii=False)
        )
        with (
            patch.object(svc, "_retrieve_knowledge", new=AsyncMock(return_value="")),
            patch(
                "clipwright.tool.registry.ToolRegistry.list_agent_callable",
                return_value=[_fake_tool()],
            ),
        ):
            result = await svc.chat(session_id, "帮我做个科技视频")

        assert result.get("status") == "brief_ready"
        brief = result.get("creative_brief") or {}
        assert brief.get("title") == "测试标题"
        assert result.get("reply", "")
        svc._llm.with_tools.assert_awaited_once()
