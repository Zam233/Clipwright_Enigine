# -*- coding: utf-8 -*-
"""Web 搜索 provider 抽象（PRD web-search-fetch-2026-10-30 M1）。

现场（2026-10-30 核查）：动画 Agent 只在事实关键词命中时搜 3 条摘要；
provider 只有 bocha/baidu 且都依赖付费 key；没配 key 时 `is_configured()` 为 False，
连工具都不出现在 Agent 工具面里——"动画自己找素材"根本无从发生。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from clipwright.config import settings
from clipwright.services import degradation as degradation_mod
from clipwright.services import web_providers as wp
from clipwright.services.web_search import WebSearchService


# ── 假 HTTP 客户端 ───────────────────────────────────────────

class _Resp:
    def __init__(self, payload=None, text="", status=200):
        self._payload = payload
        self.text = text
        self.status_code = status
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses, calls):
        # 共享同一份响应队列：`_search_local` 每个入口新建一个 client，
        # 若这里复制 list，第二次入口会重新拿到第一个响应（永远走不到 Bing）。
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, kind, url, **kw):
        self._calls.append({"kind": kind, "url": url, **kw})
        return self._responses.pop(0) if self._responses else _Resp({})

    async def post(self, url, json=None, headers=None, **kw):
        return self._next("post", url, json=json, headers=headers)

    async def get(self, url, params=None, headers=None, **kw):
        return self._next("get", url, params=params, headers=headers)


def _factory(responses, calls):
    def make(**kw):
        return _Client(responses, calls)
    return make


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.setattr(settings, "enable_web_search", True, raising=False)
    monkeypatch.setattr(settings, "web_search_allow_local", True, raising=False)
    monkeypatch.setattr(settings, "web_search_provider", "bocha", raising=False)
    monkeypatch.setattr(settings, "web_search_api_key", "", raising=False)
    monkeypatch.setattr(settings, "web_search_tavily_api_key", "", raising=False)
    monkeypatch.setattr(settings, "web_search_serper_api_key", "", raising=False)
    monkeypatch.setattr(settings, "web_search_bing_api_key", "", raising=False)
    monkeypatch.setattr(settings, "web_search_base_url", None, raising=False)
    yield


# ── 链路与配置语义 ──────────────────────────────────────────

def test_local_provider_needs_no_key():
    assert wp.configured_providers() == ["local"]
    assert wp.provider_chain() == ["local"]
    assert WebSearchService().is_configured() is True


def test_disabled_switch_kills_every_provider(monkeypatch):
    monkeypatch.setattr(settings, "enable_web_search", False, raising=False)
    assert wp.provider_chain() == []
    assert WebSearchService().is_configured() is False


def test_local_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "web_search_allow_local", False, raising=False)
    assert wp.provider_chain() == []
    assert WebSearchService().is_configured() is False


def test_primary_first_then_others_then_local(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "serper", raising=False)
    monkeypatch.setattr(settings, "web_search_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "web_search_serper_api_key", "s", raising=False)
    monkeypatch.setattr(settings, "web_search_tavily_api_key", "t", raising=False)
    chain = wp.provider_chain()
    assert chain[0] == "serper"
    assert "tavily" in chain
    assert chain[-1] == "local"


def test_specific_key_beats_global_key(monkeypatch):
    monkeypatch.setattr(settings, "web_search_api_key", "global", raising=False)
    monkeypatch.setattr(settings, "web_search_tavily_api_key", "own", raising=False)
    assert wp.provider_key(wp.PROVIDERS["tavily"]) == "own"
    assert wp.provider_key(wp.PROVIDERS["bocha"]) == "global"


# ── 本地抓取（无密钥）────────────────────────────────────────

_DDG_HTML = """
<div class="result">
  <a rel="nofollow" class="result__a" href="https://example.com/a">PCL 启动器 &amp; 下载</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://example.com/b">HMCL 仓库</a>
</div>
"""


def test_local_provider_parses_duckduckgo_html(monkeypatch):
    calls: list = []
    monkeypatch.setattr(settings, "web_search_provider", "local", raising=False)
    outcome = asyncio.run(wp.search("PCL 启动器", 5,
                                    client_factory=_factory([_Resp(text=_DDG_HTML)], calls)))
    assert outcome.provider == "local"
    assert [r["url"] for r in outcome.results] == ["https://example.com/a",
                                                  "https://example.com/b"]
    assert "PCL 启动器" in outcome.results[0]["title"]


def test_local_provider_falls_back_to_bing(monkeypatch):
    calls: list = []
    bing_html = '<h2><a href="https://example.com/c">结果 C</a></h2>'
    monkeypatch.setattr(settings, "web_search_provider", "local", raising=False)
    outcome = asyncio.run(wp.search("x", 3, client_factory=_factory(
        [_Resp(text="<html>no results</html>"), _Resp(text=bing_html)], calls)))
    assert outcome.provider == "local"
    assert outcome.results[0]["url"] == "https://example.com/c"


# ── 付费 provider 的请求形状 ─────────────────────────────────

def test_tavily_request_shape(monkeypatch):
    calls: list = []
    monkeypatch.setattr(settings, "web_search_provider", "tavily", raising=False)
    monkeypatch.setattr(settings, "web_search_tavily_api_key", "tk", raising=False)
    payload = {"results": [{"title": "T", "url": "https://t.example", "content": "摘要"}]}
    outcome = asyncio.run(wp.search("q", 2, client_factory=_factory([_Resp(payload)], calls)))
    assert outcome.provider == "tavily"
    assert calls[0]["json"]["api_key"] == "tk"
    assert outcome.results[0]["snippet"] == "摘要"


def test_serper_request_shape(monkeypatch):
    calls: list = []
    monkeypatch.setattr(settings, "web_search_provider", "serper", raising=False)
    monkeypatch.setattr(settings, "web_search_serper_api_key", "sk", raising=False)
    payload = {"organic": [{"title": "S", "link": "https://s.example", "snippet": "sn"}]}
    outcome = asyncio.run(wp.search("q", 2, client_factory=_factory([_Resp(payload)], calls)))
    assert calls[0]["headers"]["X-API-KEY"] == "sk"
    assert outcome.results[0]["url"] == "https://s.example"


def test_bing_request_shape(monkeypatch):
    calls: list = []
    monkeypatch.setattr(settings, "web_search_provider", "bing", raising=False)
    monkeypatch.setattr(settings, "web_search_bing_api_key", "bk", raising=False)
    payload = {"webPages": {"value": [{"name": "B", "url": "https://b.example"}]}}
    outcome = asyncio.run(wp.search("q", 2, client_factory=_factory([_Resp(payload)], calls)))
    assert calls[0]["headers"]["Ocp-Apim-Subscription-Key"] == "bk"
    assert outcome.results[0]["title"] == "B"


# ── 失败如实降级 ─────────────────────────────────────────────

def test_no_provider_notes_degradation(monkeypatch):
    monkeypatch.setattr(settings, "enable_web_search", False, raising=False)
    sink: list = []
    with degradation_mod.degradation_scope(sink):
        assert asyncio.run(WebSearchService().search("q")) == []
    assert any(d.kind == "web_search_unavailable" for d in sink)


def test_all_providers_fail_notes_degradation(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "local", raising=False)
    sink: list = []
    calls: list = []
    with degradation_mod.degradation_scope(sink):
        asyncio.run(WebSearchService().search(
            "q", client_factory=_factory([_Resp(text="<html></html>")] * 4, calls)))
    kinds = [d.kind for d in sink]
    assert "web_search_unavailable" in kinds
    detail = next(d.detail for d in sink if d.kind == "web_search_unavailable")
    assert "all_providers_failed" in detail
