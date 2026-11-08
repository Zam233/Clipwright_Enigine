"""OpenAI 兼容 LLM Provider 插件（Batch 2 / B2-T2）。

**它接管什么**：`openai` 与 `ollama` 两个 `llm_provider` 贡献。插件加载后
`providers/builtin_registry.register_builtin_once()` 会按 id 逐条让位，内置不再
注册这两个 id；插件被禁用后内置立刻补位（见 `clipwright/providers/builtin_registry.py`）。

**实现位置**：就在本目录（`provider.py`，B7-T7.2 迁移完成）。`build_client` 仍延后
引用 `clipwright.services.llm.OpenAIChat`（传输层构造属宿主能力，既有打桩继续有效）。

**声明面**：`plugin.yaml` 里的 `config_schema` / `capabilities` 是前端表单与调度判断的
唯一来源（B1-T2 的一级字段），本文件不重复描述。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class OpenACompatibleProviderPlugin(BasePlugin):
    """OpenAI 兼容 provider（openai / ollama）。"""

    def contributions(self) -> list[Any]:
        from plugins.llm_openai_compatible.provider import OllamaProvider, OpenAIProvider

        return [
            Contribution(
                ContributionKind.LLM_PROVIDER, "openai", OpenAIProvider(),
                meta={"name": "openai", "compatible": True},
            ),
            Contribution(
                ContributionKind.LLM_PROVIDER, "ollama", OllamaProvider(),
                meta={"name": "ollama", "compatible": True, "local": True},
            ),
        ]
