"""Anthropic LLM Provider（方案 B-1）。"""
from __future__ import annotations

from typing import Any, Mapping

from clipwright.providers.base import ProviderCapabilities


class AnthropicProvider:
    """Anthropic Messages 客户端（isobase 封装）。

    只做「构建客户端 + 声明能力」；重试/解析/工具循环留在 services/llm.py。
    """

    name = "anthropic"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calls=True, json_mode=False, vision=True,
            max_context_tokens=200_000, max_concurrency=4,
        )

    def default_model(self) -> str:
        from clipwright.config import settings
        return settings.llm_model

    def uses_system_kwarg(self) -> bool:
        """anthropic 的 with_tools 消息规范：system 走独立 kwarg。"""
        return True

    def normalize_messages(self, user_prompt: str, system_prompt: str) -> list[dict[str, Any]]:
        """structured_output 路径的规范：system 拼进 user（与既有行为一致）。"""
        return [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}]

    def build_client(self, *, api_key: str | None, base_url: str | None, model: str,
                     instructions: str, max_tokens: int, flash: bool,
                     timeout_sec: float = 0.0, proxy: str = "",
                     headers: Mapping[str, Any] | None = None) -> Any:
        # 从 services.llm 延后取符号——与核心的 isobase 导入路径一致，
        # 且既有测试对 clipwright.services.llm.AnthropicMessages 的打桩保持有效
        from clipwright.providers.transport import apply_transport
        from clipwright.services import llm as _llm_mod
        AnthropicMessages = getattr(_llm_mod, "AnthropicMessages")
        client = AnthropicMessages(
            api_key=api_key, base_url=base_url, default_model=model,
            instructions=instructions, conversation_mode=False, max_tokens=max_tokens,
        )
        # B4：超时/代理/自定义头必须落到传输层（isobase 构造时会丢弃它们）
        return apply_transport(client, timeout_sec=timeout_sec, proxy=proxy, headers=headers)
