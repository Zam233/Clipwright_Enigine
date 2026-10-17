"""B4 测试：Provider 传输层（超时/代理/自定义头）+ 嵌入/重排序 provider。"""
from __future__ import annotations

import pytest

from clipwright.providers.embedding import (
    RemoteEmbedder,
    embedding_provider_names,
    resolve_embedding_provider,
)
from clipwright.providers.rerank import (
    parse_rerank_response,
    rerank_provider_names,
    resolve_rerank_provider,
)
from clipwright.providers.transport import apply_transport, build_http_client


class _FakeSDK:
    """模拟 openai/anthropic SDK 客户端：记录构造入参，并保留 api_key/base_url 属性。"""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.api_key = kwargs.get("api_key")
        self.base_url = kwargs.get("base_url")


class _FakeIsoClient:
    def __init__(self):
        self.client = _FakeSDK(api_key="sk-x", base_url="https://api.example.com")


class TestTransport:
    def test_no_options_returns_same_client(self):
        c = _FakeIsoClient()
        assert apply_transport(c) is c
        assert apply_transport(c, timeout_sec=0, proxy="", headers=None) is c

    def test_applies_timeout_and_headers(self, monkeypatch):
        monkeypatch.setattr(_FakeSDK, "last_kwargs", {})
        c = _FakeIsoClient()
        apply_transport(c, timeout_sec=42.0, headers={"X-Tenant": "cw"})
        assert _FakeSDK.last_kwargs["timeout"] == 42.0
        assert _FakeSDK.last_kwargs["default_headers"] == {"X-Tenant": "cw"}
        assert _FakeSDK.last_kwargs["api_key"] == "sk-x"
        assert _FakeSDK.last_kwargs["base_url"] == "https://api.example.com"

    def test_proxy_builds_http_client(self, monkeypatch):
        monkeypatch.setattr(_FakeSDK, "last_kwargs", {})
        sentinel = object()
        monkeypatch.setattr(
            "clipwright.providers.transport.build_http_client", lambda proxy: sentinel,
        )
        apply_transport(_FakeIsoClient(), proxy="http://127.0.0.1:7890")
        assert _FakeSDK.last_kwargs["http_client"] is sentinel

    def test_absent_inner_client_is_noop(self):
        class Bare:
            pass
        b = Bare()
        assert apply_transport(b, timeout_sec=5) is b

    def test_build_http_client_without_proxy(self):
        assert build_http_client("") is None

    def test_build_http_client_with_proxy(self):
        client = build_http_client("http://127.0.0.1:7890")
        assert client is not None
        client.close()

    def test_sdk_error_falls_back(self, monkeypatch):
        class Boom(_FakeSDK):
            fail = False

            def __init__(self, **kwargs):
                if Boom.fail:
                    raise TypeError("unexpected kwarg")
                super().__init__(**kwargs)
        c = _FakeIsoClient()
        c.client = Boom(api_key="k", base_url="https://x")
        Boom.fail = True
        assert apply_transport(c, timeout_sec=1.0) is c  # 不抛异常，沿用原客户端


class TestEmbeddingProviders:
    def test_builtins_registered(self):
        assert set(embedding_provider_names()) == {"openai", "ollama", "local"}

    def test_unknown_provider_returns_none(self):
        assert resolve_embedding_provider("nope") is None

    def test_openai_provider_builds_remote_embedder(self):
        impl = resolve_embedding_provider("openai")
        emb = impl.create_embedder(
            model="text-embedding-3-small", api_key="sk-1",
            base_url="https://api.example.com", timeout_sec=30.0,
            proxy="", headers={"X-A": "1"},
        )
        assert isinstance(emb, RemoteEmbedder)
        assert emb.dim == 1536

    def test_remote_embedder_sdk_kwargs(self, monkeypatch):
        captured: dict = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.embeddings = self

            def create(self, input, model):  # pragma: no cover - 不由本用例触发
                raise AssertionError("not called")

        monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
        emb = RemoteEmbedder(model="m", api_key="sk-1", base_url="https://x",
                             timeout_sec=15.0, proxy="", headers={"X-A": "1"})
        emb._sdk()
        assert captured["timeout"] == 15.0
        assert captured["default_headers"] == {"X-A": "1"}
        assert captured["api_key"] == "sk-1"
        assert captured["base_url"] == "https://x"

    def test_remote_embedder_unknown_model_defaults_1536(self):
        assert RemoteEmbedder(model="whatever", api_key=None, base_url=None).dim == 1536

    def test_remote_embedder_known_model_dims(self):
        assert RemoteEmbedder(model="bge-m3", api_key=None, base_url=None).dim == 1024
        assert RemoteEmbedder(model="text-embedding-3-large", api_key=None,
                              base_url=None).dim == 3072


class TestRerankProviders:
    def test_builtins_registered(self):
        assert set(rerank_provider_names()) == {"http", "local"}

    def test_unknown_provider_returns_none(self):
        assert resolve_rerank_provider("nope") is None

    def test_http_provider_creates_reranker(self):
        impl = resolve_rerank_provider("http")
        r = impl.create_reranker(model="rerank-v1", api_key="sk", base_url="https://x",
                                 timeout_sec=9.0, proxy="", headers={"X-A": "1"})
        assert r._timeout_sec == 9.0
        assert r._headers == {"X-A": "1"}

    def test_parse_results_format(self):
        data = {"results": [{"index": 2, "relevance_score": 0.9},
                            {"index": 0, "relevance_score": 0.4}]}
        assert parse_rerank_response(data, 3) == [(2, 0.9), (0, 0.4)]

    def test_parse_alt_formats_and_bounds(self):
        assert parse_rerank_response({"output": {"results": [{"index": 1, "score": 2}]}}, 2) == []  # output 非列表
        assert parse_rerank_response({"data": [{"index": 1, "score": 0.5}]}, 2) == [(1, 0.5)]
        assert parse_rerank_response([0.3, 0.7], 2) == [(1, 0.7), (0, 0.3)]
        assert parse_rerank_response({"data": [{"index": 9, "score": 1}]}, 2) == []  # 越界索引丢弃

    def test_parse_empty(self):
        assert parse_rerank_response({}, 0) == []
        assert parse_rerank_response("boom", 1) == []

    @pytest.mark.asyncio
    async def test_http_reranker_returns_scores(self, monkeypatch):
        import httpx

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{"index": 1, "relevance_score": 0.8}]}

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                FakeAsyncClient.last = {"url": url, "json": json, "headers": headers}
                return FakeResp()

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
        impl = resolve_rerank_provider("http")
        r = impl.create_reranker(model="m", api_key="sk-1", base_url="https://x/api/",
                                 timeout_sec=5.0, proxy="", headers={"X-A": "1"})
        out = await r.rerank(query="q", documents=["a", "b"], top_n=1)
        assert out == [(1, 0.8)]
        assert FakeAsyncClient.last["url"] == "https://x/api"
        assert FakeAsyncClient.last["headers"]["Authorization"] == "Bearer sk-1"
        assert FakeAsyncClient.last["headers"]["X-A"] == "1"

    @pytest.mark.asyncio
    async def test_http_reranker_failure_returns_empty(self, monkeypatch):
        import httpx

        class BoomClient:
            def __init__(self, **kwargs): ...

            async def __aenter__(self):
                raise RuntimeError("connect failed")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(httpx, "AsyncClient", BoomClient)
        impl = resolve_rerank_provider("http")
        r = impl.create_reranker(model="m", api_key=None, base_url="https://x",
                                 timeout_sec=0.0, proxy="", headers=None)
        assert await r.rerank(query="q", documents=["a"], top_n=1) == []
