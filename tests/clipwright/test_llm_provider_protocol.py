"""M4 测试：LLM Provider 协议化（B-0 符合性 / B-1a 单一构造点 / 能力协商）。

不依赖 isobase：用假客户端类打桩 `clipwright.services.llm` 的模块符号——
与既有 test_llm.py 的打桩点一致，同时可断言「传参快照」。
"""
from __future__ import annotations

from typing import Any

import pytest

from clipwright.providers.base import LLMProvider, ProviderCapabilities
from clipwright.providers.llm import (
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
    ensure_llm_providers_registered,
)


class _FakeAnthropic:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.default_model = kwargs.get("default_model")
        self.instructions = kwargs.get("instructions")


class _FakeOpenAI(_FakeAnthropic):
    pass


@pytest.fixture(autouse=True)
def _patch_clients(monkeypatch):
    import clipwright.services.llm as llm_mod

    monkeypatch.setattr(llm_mod, "ISOBASE_AVAILABLE", True)
    monkeypatch.setattr(llm_mod, "AnthropicMessages", _FakeAnthropic)
    monkeypatch.setattr(llm_mod, "OpenAIChat", _FakeOpenAI)
    llm_mod.clear_client_cache()
    yield
    llm_mod.clear_client_cache()


class TestProviderConformance:
    def test_builtin_providers_satisfy_protocol(self):
        ensure_llm_providers_registered()
        for impl in (AnthropicProvider(), OpenAIProvider(), OllamaProvider()):
            assert isinstance(impl, LLMProvider), f"{impl.name} 不满足 LLMProvider 协议"

    def test_capabilities_declared(self):
        assert AnthropicProvider().capabilities("x").tool_calls is True
        assert AnthropicProvider().capabilities("x").streaming is True
        assert OllamaProvider().capabilities("x").vision is False
        assert OpenAIProvider().capabilities("x").json_mode is True


class TestSingleConstructionPoint:
    def test_anthropic_construction_snapshot(self):
        from clipwright.services.llm import LLMService

        client = LLMService._construct_client(
            "anthropic", api_key="k1", base_url=None, model="claude-x",
            instructions="INST", max_tokens=8192, flash=False,
        )
        assert isinstance(client, _FakeAnthropic)
        assert client.kwargs == {
            "api_key": "k1", "base_url": None, "default_model": "claude-x",
            "instructions": "INST", "conversation_mode": False, "max_tokens": 8192,
        }

    def test_openai_and_ollama_construction(self):
        from clipwright.services.llm import LLMService

        c1 = LLMService._construct_client(
            "openai", api_key="k2", base_url="https://api.x", model="gpt-x",
            instructions="I", max_tokens=8192, flash=False,
        )
        assert isinstance(c1, _FakeOpenAI)
        c2 = LLMService._construct_client(
            "ollama", api_key=None, base_url="http://localhost:11434", model="llama3",
            instructions="I", max_tokens=8192, flash=False,
        )
        assert isinstance(c2, _FakeOpenAI)

    def test_static_build_client_delegates(self):
        """静态入口与实例入口共用同一构造点与缓存。"""
        from clipwright.services.llm import LLMService

        c1 = LLMService.build_client("anthropic", "k3", "m3", "")
        c2 = LLMService.build_client("anthropic", "k3", "m3", "")
        assert c1 is c2  # 共享缓存命中

    def test_unknown_provider_lists_registered(self):
        from clipwright.services.llm import LLMService

        with pytest.raises(ValueError) as ei:
            LLMService._construct_client(
                "nope", api_key=None, base_url=None, model="m",
                instructions="", max_tokens=8192, flash=False,
            )
        msg = str(ei.value)
        assert "未安装 LLM provider" in msg and "anthropic" in msg


class TestCapabilityNegotiation:
    def test_provider_without_tool_calls_raises(self, monkeypatch):
        """能力协商（B-4）：声明不支持工具 → 明确报错而非静默降级。"""
        from clipwright.plugins.contribution import (
            BUILTIN_OWNER,
            Contribution,
            ContributionKind,
            ContributionRegistry,
        )
        from clipwright.services.llm import LLMService

        class _NoTools:
            name = "no-tools"

            def capabilities(self, model: str) -> ProviderCapabilities:
                return ProviderCapabilities(tool_calls=False)

            def uses_system_kwarg(self) -> bool:
                return False

            def normalize_messages(self, user_prompt: str, system_prompt: str):
                return []

            def build_client(self, **kwargs: Any) -> Any:  # pragma: no cover
                return None

            def default_model(self) -> str:
                return "m"

        ContributionRegistry.register(
            BUILTIN_OWNER,
            [Contribution(ContributionKind.LLM_PROVIDER, "no-tools", _NoTools())],
            dispatch=False,
        )
        try:
            svc = LLMService()
            svc.provider = "no-tools"
            import asyncio

            with pytest.raises(ValueError, match="不支持工具调用"):
                asyncio.run(svc.with_tools(
                    system_prompt="s", user_prompt="u",
                    tool_executor=lambda *a, **k: None, tools=[],
                ))
        finally:
            ContributionRegistry.clear_all()
            ensure_llm_providers_registered()
