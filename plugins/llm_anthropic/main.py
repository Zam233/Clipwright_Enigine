"""Anthropic LLM Provider 插件（Batch 2 / B2-T3）。

**接管方式**：注册 `llm_provider:anthropic`。插件加载后内置 anthropic 让位
（`register_builtin_once()` 按 id 逐条过滤）；禁用插件后内置立刻补位，角色绑定不会
因为插件消失而解析不到 provider。

**协议实现位置**：与 `llm_openai_compatible` 同策略——适配类暂留
`clipwright/providers/llm/anthropic_provider.py`，W2 收尾再搬进本目录（PRD §5.3 的
迁移过渡期）。本插件先接管**注册与声明**，让 `config_schema` / `capabilities` 成为
前端与调度的唯一真相。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class AnthropicProviderPlugin(BasePlugin):
    """Anthropic provider（Messages API）。"""

    def contributions(self) -> list[Any]:
        from clipwright.providers.llm.anthropic_provider import AnthropicProvider

        return [
            Contribution(
                ContributionKind.LLM_PROVIDER, "anthropic", AnthropicProvider(),
                meta={"name": "anthropic", "messages_api": True},
            ),
        ]
