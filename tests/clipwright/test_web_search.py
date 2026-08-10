"""WebSearchService / WebFetchService 单元测试（B2，见 docs/agent-search-cancel.md）。

覆盖：未配置门控、Bocha/Baidu 归一化、双 provider 失败兜底、fetch 各分支、
模块级单例。所有对外接口均不抛异常。

注：pyproject 已启用 pytest-asyncio asyncio_mode="auto"，async def test_ 直接执行。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from clipwright.config import settings
from clipwright.services.web_search import (
    WebFetchService,
    WebSearchService,
    get_web_fetch_service,
    get_web_search_service,
)


# ── mock 工具 ─────────────────────────────────────────────


def _mock_client(
    post_response: MagicMock | None = None,
    post_side_effect: Exception | None = None,
    get_response: MagicMock | None = None,
    get_side_effect: Exception | None = None,
) -> MagicMock:
    """构造支持 async with 的 httpx.AsyncClient mock（post/get 可独立配置）。"""
    client = MagicMock()
    if post_side_effect is not None:
        client.post = AsyncMock(side_effect=post_side_effect)
    else:
        client.post = AsyncMock(return_value=post_response)
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    else:
        client.get = AsyncMock(return_value=get_response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    resp.text = ""
    resp.headers = {}
    return resp


def _enable_search(monkeypatch) -> None:
    """打开搜索配置（settings 为模块级单例，monkeypatch 自动恢复）。"""
    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "test-key")
    monkeypatch.setattr(settings, "web_search_timeout", 15)


# ── WebSearchService ──────────────────────────────────────


async def test_not_configured_returns_empty(monkeypatch) -> None:
    """未配置（enable_web_search=False）→ search() 直接返回 []，不发请求。"""
    monkeypatch.setattr(settings, "enable_web_search", False)
    svc = WebSearchService()
    assert svc.is_configured() is False
    with patch("httpx.AsyncClient") as cls:
        results = await svc.search("随便搜")
    assert results == []
    cls.assert_not_called()


async def test_bocha_provider_normalizes_results(monkeypatch) -> None:
    """Bocha 主 provider：mock 标准 data.data.webPages.value[] → 归一化 {title,url,snippet,score}。"""
    _enable_search(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "bocha")
    payload = {
        "data": {
            "webPages": {
                "value": [
                    {"name": "标题A", "url": "https://a.com", "snippet": "摘要A", "score": 0.9},
                    {"name": "标题B", "url": "https://b.com", "snippet": "摘要B"},
                ]
            }
        }
    }
    client = _mock_client(post_response=_json_response(payload))
    with patch("httpx.AsyncClient", return_value=client):
        results = await WebSearchService().search("测试")

    assert results == [
        {"title": "标题A", "url": "https://a.com", "snippet": "摘要A", "score": 0.9},
        {"title": "标题B", "url": "https://b.com", "snippet": "摘要B", "score": 0.0},
    ]
    # 请求头必须带 Bearer 鉴权
    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"]["query"] == "测试"


async def test_bocha_shallow_nesting_variant(monkeypatch) -> None:
    """Bocha 响应嵌套变体 data.webPages.value[] 也能解析。"""
    _enable_search(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "bocha")
    payload = {"webPages": {"value": [{"name": "T", "url": "https://t.com", "snippet": "S"}]}}
    client = _mock_client(post_response=_json_response(payload))
    with patch("httpx.AsyncClient", return_value=client):
        results = await WebSearchService().search("q")
    assert results[0]["url"] == "https://t.com"


async def test_baidu_provider_parses_and_adds_auth_prefix(monkeypatch) -> None:
    """Baidu 主 provider：mock result[] → 归一化；鉴权头自动补 bce-v3/ALTAK- 前缀。"""
    _enable_search(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "baidu")
    payload = {"result": [{"title": "百度结果", "url": "https://b.com/x", "snippet": "百度摘要"}]}
    client = _mock_client(post_response=_json_response(payload))
    with patch("httpx.AsyncClient", return_value=client):
        results = await WebSearchService().search("q")

    assert results == [{"title": "百度结果", "url": "https://b.com/x", "snippet": "百度摘要", "score": 0.0}]
    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "bce-v3/ALTAK-test-key"
    assert "X-Bce-Request-Id" in kwargs["headers"]


async def test_baidu_already_prefixed_key_unchanged(monkeypatch) -> None:
    """key 已含 bce-v3/ALTAK- 前缀时不重复添加。"""
    _enable_search(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "baidu")
    monkeypatch.setattr(settings, "web_search_api_key", "bce-v3/ALTAK-full-key")
    payload = {"webSearchResult": {"items": [{"title": "T", "url": "https://t.com"}]}}
    client = _mock_client(post_response=_json_response(payload))
    with patch("httpx.AsyncClient", return_value=client):
        results = await WebSearchService().search("q")
    assert results[0]["title"] == "T"
    assert client.post.call_args.kwargs["headers"]["Authorization"] == "bce-v3/ALTAK-full-key"


async def test_both_providers_fail_returns_empty(monkeypatch) -> None:
    """主 provider 非 2xx + 备用 provider 异常 → search() 返回 [] 且不抛异常。"""
    _enable_search(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "bocha")

    def _http_error() -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500 Server Error",
                request=httpx.Request("POST", "https://api.bochaai.com/v1/web-search"),
                response=httpx.Response(500),
            )
        )
        return resp

    def _timeout() -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=httpx.TimeoutException("timed out"))
        return resp

    client = _mock_client()
    client.post = AsyncMock(side_effect=[_http_error(), _timeout()])  # bocha 500 → baidu 超时
    with patch("httpx.AsyncClient", return_value=client):
        results = await WebSearchService().search("q")

    assert results == []
    assert client.post.await_count == 2  # 主 + 备都尝试过


async def test_search_never_raises_on_unexpected(monkeypatch) -> None:
    """provider 返回非 JSON 结构 → 解析兜底为空，仍不抛异常。"""
    _enable_search(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "bocha")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(side_effect=ValueError("bad json"))
    client = _mock_client(post_response=resp)
    with patch("httpx.AsyncClient", return_value=client):
        results = await WebSearchService().search("q")
    assert results == []


# ── WebFetchService ───────────────────────────────────────


async def test_fetch_non_html_content_type_returns_empty() -> None:
    """非 HTML/纯文本 content-type（如 JSON）→ fetch() 返回 ""。"""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.headers = {"content-type": "application/json"}
    resp.text = '{"k": "v"}'
    client = _mock_client(get_response=resp)
    with patch("httpx.AsyncClient", return_value=client):
        out = await WebFetchService().fetch("https://x.com/a.json")
    assert out == ""


async def test_fetch_timeout_and_http_error_return_empty() -> None:
    """超时异常 / 非 2xx → fetch() 返回 ""。"""
    timeout_client = _mock_client(get_side_effect=httpx.TimeoutException("slow"))
    with patch("httpx.AsyncClient", return_value=timeout_client):
        out = await WebFetchService().fetch("https://x.com/a")
    assert out == ""

    resp = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "https://x.com/a"), response=httpx.Response(404)
        )
    )
    err_client = _mock_client(get_response=resp)
    with patch("httpx.AsyncClient", return_value=err_client):
        out = await WebFetchService().fetch("https://x.com/a")
    assert out == ""


async def test_fetch_strips_script_style_and_truncates() -> None:
    """HTML：去 script/style 块与标签、解实体、压缩空白；max_chars 截断。"""
    html_doc = (
        "<html><head><script>var x = 1;</script>"
        "<style>.cls { color: red }</style></head>"
        "<body><p>Hello   <b>world</b> &amp; more</p>"
        "<p>Second paragraph here.</p></body></html>"
    )
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.headers = {"content-type": "text/html; charset=utf-8"}
    resp.text = html_doc
    client = _mock_client(get_response=resp)
    with patch("httpx.AsyncClient", return_value=client):
        out = await WebFetchService().fetch("https://x.com/a")
        truncated = await WebFetchService().fetch("https://x.com/a", max_chars=10)

    assert "var x" not in out
    assert ".cls" not in out
    assert out == "Hello world & more Second paragraph here."
    assert len(truncated) == 10
    assert truncated == out[:10]


# ── 模块级单例 ────────────────────────────────────────────


def test_module_level_singleton() -> None:
    """get_web_search_service()/get_web_fetch_service() 跨调用返回同一实例。"""
    assert get_web_search_service() is get_web_search_service()
    assert get_web_fetch_service() is get_web_fetch_service()
    assert isinstance(get_web_search_service(), WebSearchService)
    assert isinstance(get_web_fetch_service(), WebFetchService)
