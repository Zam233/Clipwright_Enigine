"""WebSearchTool / WebFetchTool 单元测试（B3，见 docs/agent-search-cancel.md）。

覆盖：is_available 配置门控、execute 正常/未配置/空结果分支、工具注册。
工具层不抛异常：异常路径均返回 DEPENDENCY_MISSING 与空结果。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from clipwright.config import settings
from clipwright.schema.tool import ToolStatus
from clipwright.tool import register_builtin_tools
from clipwright.tool.registry import ToolRegistry
from clipwright.tool.web_search_tool import WebFetchTool, WebSearchTool


# ── mock 工具 ─────────────────────────────────────────────


def _stub_search_service(
    configured: bool = True, results: list[dict] | None = None
) -> MagicMock:
    """构造 WebSearchService stub：is_configured() + search()。"""
    svc = MagicMock()
    svc.is_configured.return_value = configured
    svc.search = AsyncMock(return_value=results or [])
    return svc


def _stub_fetch_service(content: str = "") -> MagicMock:
    """构造 WebFetchService stub：fetch()。"""
    svc = MagicMock()
    svc.fetch = AsyncMock(return_value=content)
    return svc


# ── is_available 配置门控 ─────────────────────────────────


async def test_search_is_available_false_when_not_configured(monkeypatch) -> None:
    """未配置（enable_web_search=False）→ WebSearchTool 不可用。"""
    monkeypatch.setattr(settings, "enable_web_search", False)
    monkeypatch.setattr(settings, "web_search_api_key", "")
    assert WebSearchTool().is_available() is False


async def test_search_is_available_true_when_configured(monkeypatch) -> None:
    """已配置（enable_web_search=True + api_key）→ WebSearchTool 可用。"""
    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    assert WebSearchTool().is_available() is True


async def test_fetch_is_available_gated_by_config(monkeypatch) -> None:
    """WebFetchTool 可用性与搜索配置同开关。"""
    monkeypatch.setattr(settings, "enable_web_search", False)
    monkeypatch.setattr(settings, "web_search_api_key", "")
    assert WebFetchTool().is_available() is False

    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    assert WebFetchTool().is_available() is True


# ── WebSearchTool.execute ─────────────────────────────────


async def test_execute_calls_search_and_normalizes(monkeypatch) -> None:
    """配置就绪 → execute 调用服务并归一化 {title,url,snippet}。"""
    fake_results = [
        {"title": "标题A", "url": "https://a.com", "snippet": "摘要A", "score": 0.9},
        {"title": "标题B", "url": "https://b.com", "snippet": "", "score": 0.0},
    ]
    stub = _stub_search_service(configured=True, results=fake_results)
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_search_service", lambda: stub
    )

    result = await WebSearchTool().execute("测试", max_results=2)

    assert result.status == ToolStatus.SUCCESS
    assert result.tool_name == "web_search"
    assert result.output["results"] == [
        {"title": "标题A", "url": "https://a.com", "snippet": "摘要A"},
        {"title": "标题B", "url": "https://b.com", "snippet": ""},
    ]
    stub.search.assert_awaited_once_with("测试", max_results=2)


async def test_execute_not_configured_returns_empty(monkeypatch) -> None:
    """未配置 → status=DEPENDENCY_MISSING，output.results=[]，不调 search。"""
    stub = _stub_search_service(configured=False)
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_search_service", lambda: stub
    )

    result = await WebSearchTool().execute("测试")

    assert result.status == ToolStatus.DEPENDENCY_MISSING
    assert result.tool_name == "web_search"
    assert result.output == {"results": []}
    stub.search.assert_not_awaited()


async def test_execute_empty_results_returns_dependency_missing(monkeypatch) -> None:
    """已配置但无结果 → status=DEPENDENCY_MISSING，output.results=[]。"""
    stub = _stub_search_service(configured=True, results=[])
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_search_service", lambda: stub
    )

    result = await WebSearchTool().execute("无结果词")

    assert result.status == ToolStatus.DEPENDENCY_MISSING
    assert result.output == {"results": []}


async def test_execute_service_exception_never_raises(monkeypatch) -> None:
    """服务层意外异常 → 不抛异常，返回 DEPENDENCY_MISSING 与空结果。"""
    stub = _stub_search_service(configured=True)
    stub.search = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_search_service", lambda: stub
    )

    result = await WebSearchTool().execute("测试")

    assert result.status == ToolStatus.DEPENDENCY_MISSING
    assert result.output == {"results": []}


# ── WebFetchTool.execute ──────────────────────────────────


async def test_fetch_execute_returns_content(monkeypatch) -> None:
    """fetch 返回正文 → status=SUCCESS，output.content=正文。"""
    stub = _stub_fetch_service(content="这是网页正文内容")
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_fetch_service", lambda: stub
    )

    result = await WebFetchTool().execute("https://a.com")

    assert result.status == ToolStatus.SUCCESS
    assert result.tool_name == "web_fetch"
    assert result.output == {"content": "这是网页正文内容"}
    stub.fetch.assert_awaited_once_with("https://a.com", max_chars=4000)


async def test_fetch_execute_empty_content_returns_dependency_missing(
    monkeypatch,
) -> None:
    """fetch 返回空串 → status=DEPENDENCY_MISSING，output.content=''。"""
    stub = _stub_fetch_service(content="")
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_fetch_service", lambda: stub
    )

    result = await WebFetchTool().execute("https://a.com")

    assert result.status == ToolStatus.DEPENDENCY_MISSING
    assert result.output == {"content": ""}


# ── 注册 ──────────────────────────────────────────────────


def test_tools_registered_in_toolregistry() -> None:
    """register_builtin_tools() 后，web_search / web_fetch 均注册到 ToolRegistry。"""
    register_builtin_tools()
    assert ToolRegistry.get("web_search") is not None
    assert ToolRegistry.get("web_fetch") is not None
    assert isinstance(ToolRegistry.get("web_search"), WebSearchTool)
    assert isinstance(ToolRegistry.get("web_fetch"), WebFetchTool)
