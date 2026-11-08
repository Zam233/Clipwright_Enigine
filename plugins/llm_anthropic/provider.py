"""Anthropic Provider 实现（**能力方**：插件拥有实现，engine 只留协议）。

2026-11-08 能力迁移（审计 B7-T7.2）：类从 `clipwright/providers/llm/` 搬到这里；
engine 侧 `providers/llm` 只保留协议说明。`build_client` 仍延后引用
`clipwright.services.llm` 里的传输层构造（宿主能力，既有打桩继续有效）。
"""
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
            # Anthropic 有权威模型清单（/v1/models）：来源没清单时不放行任意 id
            arbitrary_models=False,
            # 支持 extended thinking，但预算映射由 adapter 语义决定（B5 单独处理）
            reasoning_effort=True,
            # thinking block 以「思考块」形式返回（非逐 token），故 streaming=False
            reasoning_content=True,
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

    def vision_message_format(self) -> str:
        """Anthropic：image 用 {type: image, source: {base64...}} 块。"""
        return "anthropic"

    def model_listing_spec(self) -> dict[str, Any]:
        """Anthropic：/v1/models + x-api-key 头 + anthropic-version。"""
        return {
            "base": "https://api.anthropic.com",
            "path": "/v1/models",
            "auth": "x-api-key",
            "extra_headers": {"anthropic-version": "2023-06-01"},
        }

    def tool_result_style(self) -> str:
        """工具结果消息风格：Anthropic 用 content 数组里的 tool_use / tool_result 块。"""
        return "anthropic"

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
