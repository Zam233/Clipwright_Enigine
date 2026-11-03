"""Rerank Provider 插件（Batch 4 / B4-T2）。

接管 `http` / `local` 两个 `rerank_provider`；插件禁用后内置补位。
适配类暂留 `providers/rerank/`（同 Batch 2/3 的分阶段策略）。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class RerankProvidersPlugin(BasePlugin):
    """内置重排序 provider 的插件化外壳。"""

    def contributions(self) -> list[Any]:
        from clipwright.providers.rerank import HttpRerankProvider, LocalRerankProvider

        return [
            Contribution(ContributionKind.RERANK_PROVIDER, "http", HttpRerankProvider(),
                         meta={"name": "http"}),
            Contribution(ContributionKind.RERANK_PROVIDER, "local", LocalRerankProvider(),
                         meta={"name": "local", "offline": True}),
        ]
