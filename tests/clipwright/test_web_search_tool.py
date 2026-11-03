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


def _stub_fetch_service(
    *, ok: bool = True, content: str = "", kind: str = "html",
    content_type: str = "text/html", error: str = "",
) -> MagicMock:
    """构造 WebFetchService stub。

    2026-10-31 起 `WebFetchTool` 走**统一入口 `probe()`**（先判类型再分流：
    网页出正文、图片/视频落盘、JSON 出原文），不再直接调 `fetch()`，
    所以这里打桩的是 `probe()` 而不是 `fetch()`。
    """
    svc = MagicMock()
    svc.probe = AsyncMock(return_value={
        "ok": ok, "kind": kind, "content_type": content_type,
        "content": content, "bytes": len(content.encode("utf-8")),
        "final_url": "https://a.com", "error": error,
    })
    return svc


# ── is_available / is_configured 语义（2026-10 调整）──────────
#
# 契约变化：`is_available()` 只表示"工具本体可用（无外部二进制依赖）"，
# **配置缺失不再等于不可用**——否则主 Agent 的 Kernel 工具面里根本没有 web_search，
# 它永远不知道自己能联网。阶段 Agent 的可见性改由 `is_configured()` 把关
# （见 ToolRegistry.list_agent_callable）。


async def test_search_is_available_even_when_not_configured(monkeypatch) -> None:
    """未配置 → 工具仍可用，但 is_configured() 为 False。"""
    monkeypatch.setattr(settings, "enable_web_search", False)
    monkeypatch.setattr(settings, "web_search_api_key", "")
    tool = WebSearchTool()
    assert tool.is_available() is True
    assert tool.is_configured() is False


async def test_search_is_configured_when_configured(monkeypatch) -> None:
    """已配置（enable_web_search=True + api_key）→ 可用且已配置。"""
    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    tool = WebSearchTool()
    assert tool.is_available() is True
    assert tool.is_configured() is True


async def test_fetch_is_configured_without_search_key(monkeypatch) -> None:
    """抓取不依赖搜索 API key（2026-10-30 修正）。

    旧契约是"fetch 跟随搜索开关"，后果：没配 key 时 `resolve("web_fetch")` 解析不到，
    所有 Agent 都拿不到"抓一个指定 URL"的能力。抓取是本地 HTTP + 正文抽取，
    真失败由 execute 如实回报。
    """
    monkeypatch.setattr(settings, "enable_web_search", False)
    monkeypatch.setattr(settings, "web_search_api_key", "")
    tool = WebFetchTool()
    assert tool.is_available() is True
    assert tool.is_configured() is True

    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    tool = WebFetchTool()
    assert tool.is_available() is True
    assert tool.is_configured() is True


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
    # 2026-10 起工具层「归一化但不丢字段」：保留 `score`（DeepSeek 的 rerank_score 等
    # 对 agent 判断"这条靠不靠谱、要不要读正文"很关键）。所以这里断言三个核心字段
    # 仍然在，且额外字段被原样透传。
    results = result.output["results"]
    assert [{"title": r["title"], "url": r["url"], "snippet": r["snippet"]} for r in results] == [
        {"title": "标题A", "url": "https://a.com", "snippet": "摘要A"},
        {"title": "标题B", "url": "https://b.com", "snippet": ""},
    ]
    assert results[0]["score"] == 0.9, "score 应被透传而不是丢掉"
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
    """probe 判定为网页且拿到正文 → status=SUCCESS，output.content=正文。"""
    stub = _stub_fetch_service(content="这是网页正文内容")
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_fetch_service", lambda: stub
    )

    result = await WebFetchTool().execute("https://a.com")

    assert result.status == ToolStatus.SUCCESS
    assert result.tool_name == "web_fetch"
    assert result.output["content"] == "这是网页正文内容"
    assert result.output["kind"] == "html"
    assert result.output["fetched"] is True
    stub.probe.assert_awaited_once_with("https://a.com", max_chars=4000, want_body=True)


async def test_fetch_execute_empty_content_still_success(monkeypatch) -> None:
    """探测成功但正文本为空（空页面/纯结构页）→ 仍是 SUCCESS，不算依赖缺失。

    旧契约是"空正文 = DEPENDENCY_MISSING"，但它把"这一页没正文"和"抓取能力没装"
    混成一件事；现在只有 `probe.ok=False`（HTTP 错/超时/非文本）才算依赖不可用。
    """
    stub = _stub_fetch_service(ok=True, content="")
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_fetch_service", lambda: stub
    )

    result = await WebFetchTool().execute("https://a.com")

    assert result.status == ToolStatus.SUCCESS
    assert result.output["content"] == ""


async def test_fetch_execute_probe_failed_returns_dependency_missing(monkeypatch) -> None:
    """probe 失败（HTTP 4xx/5xx、超时）→ DEPENDENCY_MISSING + 带上探到的 error。"""
    stub = _stub_fetch_service(ok=False, error="HTTP 500")
    monkeypatch.setattr(
        "clipwright.tool.web_search_tool.get_web_fetch_service", lambda: stub
    )

    result = await WebFetchTool().execute("https://a.com")

    assert result.status == ToolStatus.DEPENDENCY_MISSING
    assert result.output["fetched"] is False
    assert result.output["error"] == "HTTP 500"


# ── 注册 ──────────────────────────────────────────────────


def test_tools_registered_in_toolregistry() -> None:
    """register_builtin_tools() 后，web_search / web_fetch 均注册到 ToolRegistry。"""
    register_builtin_tools()
    assert ToolRegistry.get("web_search") is not None
    assert ToolRegistry.get("web_fetch") is not None
    assert isinstance(ToolRegistry.get("web_search"), WebSearchTool)
    assert isinstance(ToolRegistry.get("web_fetch"), WebFetchTool)
