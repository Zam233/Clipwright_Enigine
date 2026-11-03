"""OpenAI 兼容 LLM Provider 插件（Batch 2 / B2-T2）。

**它接管什么**：`openai` 与 `ollama` 两个 `llm_provider` 贡献。插件加载后
`providers/builtin_registry.register_builtin_once()` 会按 id 逐条让位，内置不再
注册这两个 id；插件被禁用后内置立刻补位（见 `clipwright/providers/builtin_registry.py`）。

**为什么实现还在 engine**：本仓的 OpenAI 协议适配类（`providers/llm/openai_provider.py`）
只有 68 行、只依赖 `providers.base` 与传输层，B2 先把**注册**插件化，避免一次性搬类
把 `services.llm` 的打桩链打断（既有测试大量 monkeypatch `clipwright.services.llm.OpenAIChat`）。
W2 收尾时再把适配类搬进本目录，engine 侧只留 fallback shim；届时本文件改一行 import 即可。
这一边界写在 PRD §5.3「迁移阶段保留内置实现作为 fallback」下，不是遗漏。

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
        from clipwright.providers.llm.openai_provider import OllamaProvider, OpenAIProvider

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
