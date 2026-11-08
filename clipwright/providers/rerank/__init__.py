"""Rerank Provider **协议与注册表访问器**（实现已迁进 `plugins/rerank_providers`）。

2026-11-08 能力迁移（审计 B7-T7.3）：engine 不再持有厂商实现、不再懒注册内置补位。
`parse_rerank_response` 属于**平台解析**（兼容多种 API 响应格式），留在这里。
"""
from __future__ import annotations

from typing import Any


def parse_rerank_response(data: Any, doc_count: int) -> list[tuple[int, float]]:
    """兼容多种重排序响应格式 → [(索引, 分数)]（降序）。"""
    raw: Any = []
    if isinstance(data, dict):
        raw = data.get("results") or data.get("output") or data.get("data") or []
    elif isinstance(data, list):
        raw = data

    out: list[tuple[int, float]] = []
    if isinstance(raw, list):
        for pos, item in enumerate(raw):
            if isinstance(item, dict):
                idx = item.get("index")
                score = item.get("relevance_score", item.get("score", item.get("relevanceScore", 0)))
                if idx is not None and 0 <= int(idx) < doc_count:
                    out.append((int(idx), float(score)))
            elif isinstance(item, (int, float)) and pos < doc_count:
                out.append((pos, float(item)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def resolve_rerank_provider(name: str) -> Any | None:
    """解析重排序 provider 实现（只读注册表）；未安装/被禁用 → None。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    impl = ContributionRegistry.resolve(ContributionKind.RERANK_PROVIDER, str(name or ""))
    return impl.object if impl is not None else None


def rerank_provider_names() -> list[str]:
    """已注册的重排序 provider 名（前端选项与错误信息共用）。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    return [c.id for _, c in ContributionRegistry.list(ContributionKind.RERANK_PROVIDER)]
