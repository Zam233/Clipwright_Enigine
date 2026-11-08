"""Embedding Provider 插件（Batch 4 / B4-T1）。

接管 `openai` / `ollama` / `local` 三个 `embedding_provider`。插件加载后内置同名实现
让位（逐 id，见 `providers/builtin_registry.py`）；插件禁用后内置补位，RAG 不会因为
插件消失而失去嵌入能力。

实现位置：**类就在本插件**（`providers.py`），engine 只留注册表访问器。
**注册与声明**（`config_schema` / `capabilities` 是前端表单与诊断的唯一来源）。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class EmbeddingProvidersPlugin(BasePlugin):
    """内置嵌入 provider 的插件化外壳。"""

    def contributions(self) -> list[Any]:
        from plugins.embedding_providers.providers import (
            LocalEmbeddingProvider,
            OllamaEmbeddingProvider,
            OpenAIEmbeddingProvider,
        )

        return [
            Contribution(ContributionKind.EMBEDDING_PROVIDER, "openai", OpenAIEmbeddingProvider(),
                         meta={"name": "openai"}),
            Contribution(ContributionKind.EMBEDDING_PROVIDER, "ollama", OllamaEmbeddingProvider(),
                         meta={"name": "ollama", "local": True}),
            Contribution(ContributionKind.EMBEDDING_PROVIDER, "local", LocalEmbeddingProvider(),
                         meta={"name": "local", "offline": True}),
        ]
