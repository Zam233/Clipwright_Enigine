"""Rerank Provider 插件（Batch 4 / B4-T2）。

接管 `http` / `local` 两个 `rerank_provider`；实现就在本插件（`providers.py`）。
插件被禁用 → 没有 rerank 能力（口径 A：能力缺失是一等状态）。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class RerankProvidersPlugin(BasePlugin):
    """内置重排序 provider 的插件化外壳。"""

    def contributions(self) -> list[Any]:
        from plugins.rerank_providers.providers import HttpRerankProvider, LocalRerankProvider

        return [
            Contribution(ContributionKind.RERANK_PROVIDER, "http", HttpRerankProvider(),
                         meta={"name": "http"}),
            Contribution(ContributionKind.RERANK_PROVIDER, "local", LocalRerankProvider(),
                         meta={"name": "local", "offline": True}),
        ]
