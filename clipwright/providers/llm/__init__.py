"""providers.llm 包 —— 内置 LLM Provider 注册（方案 B-1）。

懒注册设计：`ensure_llm_providers_registered()` 幂等，可由 main.py lifespan
与 `services/llm.py::_construct_client` 双点调用——测试/脚本环境未跑启动
流程时也能解析到内置 provider。
"""
from __future__ import annotations

from clipwright.providers.llm.anthropic_provider import AnthropicProvider
from clipwright.providers.llm.openai_provider import OllamaProvider, OpenAIProvider

__all__ = ["AnthropicProvider", "OpenAIProvider", "OllamaProvider",
           "ensure_llm_providers_registered"]


def ensure_llm_providers_registered() -> None:
    """把三个内置 LLM provider 登记进门面（幂等）。"""
    from clipwright.plugins.contribution import (
        BUILTIN_OWNER,
        Contribution,
        ContributionKind,
        ContributionRegistry,
    )

    if ContributionRegistry.resolve(ContributionKind.LLM_PROVIDER, "anthropic") is not None:
        return
    ContributionRegistry.register(BUILTIN_OWNER, [
        Contribution(ContributionKind.LLM_PROVIDER, "anthropic", AnthropicProvider()),
        Contribution(ContributionKind.LLM_PROVIDER, "openai", OpenAIProvider()),
        Contribution(ContributionKind.LLM_PROVIDER, "ollama", OllamaProvider()),
    ], dispatch=False)
