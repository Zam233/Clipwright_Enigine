"""LLM Provider **协议**（实现已迁进 `plugins/llm_anthropic` / `plugins/llm_openai_compatible`）。

2026-11-08 能力迁移（审计 B7-T7.2）：engine 不再持有 Anthropic / OpenAI / Ollama 的实现，
也不再"懒注册内置补位"。协议本身在 `clipwright/providers/base.py`（`LLMProvider` Protocol +
`llm_capabilities` / `llm_normalize_messages` / `llm_uses_system_kwarg` / `llm_tool_result_style`）。

没有 LLM 插件时：`_resolve_llm_provider()` 返回 None，调用方按"能力缺失"处理
（口径 A：能力缺失是一等状态，不是异常）。
"""
from __future__ import annotations

__all__: list[str] = []


def llm_provider_is_local(name: str) -> bool:
    """该 LLM provider 是否"本地免 key"（由插件在贡献 meta 里声明 ``local: true``）。

    B7-T7.5：engine 不再按名字判 ``== "ollama"``——"要不要 API key"是**能力方**的知识。
    未安装/被禁用 → False（按需要凭据处理，交给调用方报可读错误）。
    """
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    item = ContributionRegistry.resolve(ContributionKind.LLM_PROVIDER, str(name or ""))
    if item is None:
        return False
    return bool((item.meta or {}).get("local"))
