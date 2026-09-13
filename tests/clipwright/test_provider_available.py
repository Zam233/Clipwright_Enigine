"""B4 测试：门面驱动的 provider 选项（含插件贡献）+ 模型列表拉取代理。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import providers as providers_api
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(providers_api.router)
    return TestClient(app)


class TestAvailableProviders:
    def test_builtins_listed_per_kind(self, client):
        body = client.get("/api/providers/available").json()
        assert set(body["llm_provider"]) >= {"anthropic", "openai", "ollama"}
        assert set(body["tts_provider"]) >= {"qwen-tts", "cosyvoice", "minimax"}
        assert set(body["embedding_provider"]) >= {"openai", "ollama", "local"}
        assert set(body["rerank_provider"]) >= {"http", "local"}

    def test_role_kinds_mapping(self, client):
        body = client.get("/api/providers/available").json()
        assert body["role_kinds"] == {
            "main": "llm", "flash": "llm", "vision": "llm",
            "embed": "embedding", "rerank": "rerank", "tts": "tts",
        }

    def test_tts_alias_exposed(self, client):
        assert client.get("/api/providers/available").json()["tts_aliases"] == {
            "qwen_tts": "qwen-tts",
        }

    def test_plugin_provider_appears_then_disappears(self, client):
        """P4/P5：插件贡献的 provider 必须出现在下拉里，卸载后立即消失。"""
        item = Contribution(ContributionKind.LLM_PROVIDER, "plugin-llm", object())
        ContributionRegistry.register("plug_demo", [item], dispatch=False)
        try:
            assert "plugin-llm" in client.get("/api/providers/available").json()["llm_provider"]
            ContributionRegistry.unregister_plugin("plug_demo")
            assert "plugin-llm" not in client.get(
                "/api/providers/available"
            ).json()["llm_provider"]
        finally:
            ContributionRegistry.unregister_plugin("plug_demo")

    def test_plugin_embedding_provider_appears(self, client):
        item = Contribution(ContributionKind.EMBEDDING_PROVIDER, "plugin-emb", object())
        ContributionRegistry.register("plug_demo2", [item], dispatch=False)
        try:
            assert "plugin-emb" in client.get(
                "/api/providers/available"
            ).json()["embedding_provider"]
        finally:
            ContributionRegistry.unregister_plugin("plug_demo2")


class TestModelListingTransport:
    @pytest.mark.asyncio
    async def test_proxy_and_headers_passed(self, monkeypatch):
        import httpx

        from clipwright.providers.model_listing import list_models_for_source

        captured: dict = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "m1"}, {"id": "m2"}]}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                captured["url"] = url
                return FakeResp()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        models = await list_models_for_source("openai", {
            "base_url": "https://x/v1", "api_key": "sk-1", "proxy": "http://p:1",
            "headers": {"X-A": "1"}, "timeout_sec": 7,
        })
        assert models == ["m1", "m2"]
        assert captured["proxy"] == "http://p:1"
        assert captured["timeout"] == 7.0
        assert captured["headers"]["X-A"] == "1"
        assert captured["headers"]["Authorization"] == "Bearer sk-1"
        assert captured["url"] == "https://x/v1/models"

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_empty(self):
        from clipwright.providers.model_listing import list_models_for_source

        assert await list_models_for_source("local", {}) == []
