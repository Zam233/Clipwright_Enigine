"""OpenAI 兼容 LLM Provider（方案 B-1）—— openai 与本地 ollama。"""
from __future__ import annotations

from typing import Any

from clipwright.providers.base import ProviderCapabilities


class OpenAIProvider:
    """OpenAI Chat 客户端（isobase 封装）。"""

    name = "openai"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calls=True, json_mode=True, vision=True,
            max_context_tokens=128_000, max_concurrency=4,
        )

    def default_model(self) -> str:
        from clipwright.config import settings
        return settings.llm_model

    def uses_system_kwarg(self) -> bool:
        return False  # system 作为独立消息

    def normalize_messages(self, user_prompt: str, system_prompt: str) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def build_client(self, *, api_key: str | None, base_url: str | None, model: str,
                     instructions: str, max_tokens: int, flash: bool) -> Any:
        # 从 services.llm 延后取符号——与核心的 isobase 导入路径一致，
        # 且既有测试对 clipwright.services.llm.OpenAIChat 的打桩保持有效
        from clipwright.services import llm as _llm_mod
        OpenAIChat = getattr(_llm_mod, "OpenAIChat")
        return OpenAIChat(
            api_key=api_key, base_url=base_url, default_model=model,
            instructions=instructions, conversation_mode=False, max_tokens=max_tokens,
        )


class OllamaProvider(OpenAIProvider):
    """本地 Ollama（OpenAI 兼容端点）——默认无 JSON mode 保证、无视觉。"""

    name = "ollama"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calls=True, json_mode=False, vision=False,
            max_context_tokens=32_768, max_concurrency=1,
        )
