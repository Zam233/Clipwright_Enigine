"""OpenAI 兼容 Provider 实现（插件拥有实现；engine 只留协议）。

2026-11-08 能力迁移（审计 B7-T7.2）：类从 `clipwright/providers/llm/` 搬到这里；
engine 侧 `providers/llm` 只保留协议说明。`build_client` 仍延后引用
`clipwright.services.llm` 里的传输层构造（宿主能力，既有打桩继续有效）。
"""
from __future__ import annotations

from typing import Any, Mapping

from clipwright.providers.base import ProviderCapabilities


class OpenAIProvider:
    """OpenAI Chat 客户端（isobase 封装）。"""

    name = "openai"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calls=True, json_mode=True, vision=True,
            max_context_tokens=128_000, max_concurrency=4,
            # OpenAI 兼容网关不维护固定模型清单：来源没配清单时也接受任意 model id
            arbitrary_models=True,
            reasoning_effort=True,  # o 系列 / gpt-5 系列接受 reasoning_effort
            # deepseek / o 系列会在 delta 里回吐 reasoning_content（unified-stream PRD §5.1）
            reasoning_content=True, reasoning_streaming=True,
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

    def vision_message_format(self) -> str:
        """OpenAI 兼容：image 用 {type: image_url, image_url: {url: data URI}}。"""
        return "openai"

    def model_listing_spec(self) -> dict[str, Any]:
        """OpenAI 兼容：{base}/models + Bearer；ollama 默认本地端点。"""
        from clipwright.config import settings

        if self.name == "ollama":
            base = "http://localhost:11434/v1"
        else:
            base = str(getattr(settings, "llm_base_url", "") or "") or "https://api.openai.com/v1"
        return {"base": base, "path": "/models", "auth": "bearer"}

    def tool_result_style(self) -> str:
        """工具结果消息风格：OpenAI 用 assistant.tool_calls + role=tool 消息。"""
        return "openai"

    def build_client(self, *, api_key: str | None, base_url: str | None, model: str,
                     instructions: str, max_tokens: int, flash: bool,
                     timeout_sec: float = 0.0, proxy: str = "",
                     headers: Mapping[str, Any] | None = None) -> Any:
        # 从 services.llm 延后取符号——与核心的 isobase 导入路径一致，
        # 且既有测试对 clipwright.services.llm.OpenAIChat 的打桩保持有效
        from clipwright.providers.transport import apply_transport
        from clipwright.services import llm as _llm_mod
        OpenAIChat = getattr(_llm_mod, "OpenAIChat")
        client = OpenAIChat(
            api_key=api_key, base_url=base_url, default_model=model,
            instructions=instructions, conversation_mode=False, max_tokens=max_tokens,
        )
        # B4：超时/代理/自定义头必须落到传输层（isobase 构造时会丢弃它们）
        return apply_transport(client, timeout_sec=timeout_sec, proxy=proxy, headers=headers)


class OllamaProvider(OpenAIProvider):
    """本地 Ollama（OpenAI 兼容端点）——默认无 JSON mode 保证、无视觉。"""

    name = "ollama"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calls=True, json_mode=False, vision=False,
            max_context_tokens=32_768, max_concurrency=1,
            arbitrary_models=True,  # 本地拉什么模型都可能，没有清单可对
            reasoning_effort=False,
            # Ollama 不回吐 reasoning_content：前端必须显示「不提供思考内容」
            reasoning_content=False,
        )
