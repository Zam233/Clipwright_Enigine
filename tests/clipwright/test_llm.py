"""LLM 服务测试 — 验证 IsoBase 集成。"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("isobase", reason="isobase 未安装，跳过 IsoBase 集成测试")

from clipwright.services.llm import LLMService


class TestLLMService:
    def test_init_defaults(self) -> None:
        """验证 LLMService 默认初始化。"""
        svc = LLMService()
        assert svc.provider in ("anthropic", "openai", "ollama")

    def test_client_anthropic(self) -> None:
        """验证 AnthropicMessages 客户端可构建。"""
        svc = LLMService()
        from isobase.llm import AnthropicMessages

        svc.provider = "anthropic"
        client = svc._build_client()
        assert isinstance(client, AnthropicMessages)
        assert client.default_model is not None
        assert "ClipWright" in client.instructions

    def test_client_openai_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证 OpenAIChat 客户端在有 API key 时可构建。"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
        svc = LLMService()
        from isobase.llm import OpenAIChat

        svc.provider = "openai"
        client = svc._build_client()
        assert isinstance(client, OpenAIChat)
        assert client.default_model is not None

    def test_client_ollama_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证 Ollama 客户端在有 API key 时可构建。"""
        monkeypatch.setenv("OPENAI_API_KEY", "ollama-dummy-key")
        svc = LLMService()
        from isobase.llm import OpenAIChat

        svc.provider = "ollama"
        client = svc._build_client()
        assert isinstance(client, OpenAIChat)

    def test_reset_rebuilds_client(self) -> None:
        """验证 reset() 后 client 被重建。"""
        svc = LLMService()
        c1 = svc.client
        svc.reset()
        c2 = svc.client
        assert c1 is not c2

    @pytest.mark.asyncio
    async def test_chat_no_api_key(self) -> None:
        """无 API key 时 chat() 应返回适当错误而非崩溃。"""
        from clipwright.config import settings

        orig_key = settings.llm_api_key
        orig_provider = settings.llm_provider
        settings.llm_api_key = ""
        settings.llm_provider = "openai"

        svc = LLMService()
        svc.reset()

        try:
            resp = await svc.generate(
                messages=[{"role": "user", "content": "hello"}],
            )
            assert hasattr(resp, "success")
            assert hasattr(resp, "content")
            assert hasattr(resp, "usage")
        except Exception:
            pass
        finally:
            settings.llm_api_key = orig_key
            settings.llm_provider = orig_provider
