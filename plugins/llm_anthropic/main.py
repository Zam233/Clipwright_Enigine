"""Anthropic LLM Provider 插件（Batch 2 / B2-T3）。

**接管方式**：注册 `llm_provider:anthropic`。插件加载后内置 anthropic 让位
（`register_builtin_once()` 按 id 逐条过滤）；禁用插件后内置立刻补位，角色绑定不会
因为插件消失而解析不到 provider。

**协议实现位置**：就在本目录（`provider.py`，B7-T7.2 迁移完成）；engine 侧
`providers/llm` 只留协议说明，不再有实现类。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class AnthropicProviderPlugin(BasePlugin):
    """Anthropic provider（Messages API）。"""

    def contributions(self) -> list[Any]:
        from plugins.llm_anthropic.provider import AnthropicProvider

        return [
            Contribution(
                ContributionKind.LLM_PROVIDER, "anthropic", AnthropicProvider(),
                meta={"name": "anthropic", "messages_api": True},
            ),
        ]
