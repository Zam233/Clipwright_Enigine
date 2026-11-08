"""内置 Embedding Provider（**engine 侧只留注册表访问器**）。

2026-11-08 能力迁移（审计 B7-T7.3）：实现已搬进 `plugins/embedding_providers/providers.py`。
engine 不再持有任何厂商实现、也不再"懒注册"内置补位——

- 插件在 → `embedding_provider_names()` 有值；
- 插件被禁用 → 列表为空（口径 A：能力缺失是一等状态，调用方按"无可用嵌入"处理）。

传输层（`providers.transport`）仍是宿主能力，插件复用它。
"""
from __future__ import annotations

from typing import Any


def resolve_embedding_provider(name: str) -> Any | None:
    """解析嵌入 provider 实现（只读注册表）；未安装/被禁用 → None。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    impl = ContributionRegistry.resolve(ContributionKind.EMBEDDING_PROVIDER, str(name or ""))
    return impl.object if impl is not None else None


def embedding_provider_names() -> list[str]:
    """已注册的嵌入 provider 名（前端选项与错误信息共用）。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    return [c.id for _, c in ContributionRegistry.list(ContributionKind.EMBEDDING_PROVIDER)]
