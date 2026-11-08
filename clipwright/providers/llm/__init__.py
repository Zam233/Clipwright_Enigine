"""LLM Provider **协议**（实现已迁进 `plugins/llm_anthropic` / `plugins/llm_openai_compatible`）。

2026-11-08 能力迁移（审计 B7-T7.2）：engine 不再持有 Anthropic / OpenAI / Ollama 的实现，
也不再"懒注册内置补位"。协议本身在 `clipwright/providers/base.py`（`LLMProvider` Protocol +
`llm_capabilities` / `llm_normalize_messages` / `llm_uses_system_kwarg` / `llm_tool_result_style`）。

没有 LLM 插件时：`_resolve_llm_provider()` 返回 None，调用方按"能力缺失"处理
（口径 A：能力缺失是一等状态，不是异常）。
"""
from __future__ import annotations

__all__: list[str] = []
