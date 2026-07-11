"""MaterialRegistry — 全局素材源注册表。

管理所有注册的 MaterialSource，提供统一的 search 入口。
"""

from __future__ import annotations

from typing import Optional

from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialSearchResult


class MaterialRegistry:
    """全局素材源注册表。"""

    _instance: MaterialRegistry | None = None
    _sources: dict[str, MaterialSource] = {}

    def __new__(cls) -> MaterialRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, source: MaterialSource, plugin_id: str = "") -> None:
        """注册一个素材源。"""
        if not source.source_id:
            raise ValueError(f"MaterialSource must have a non-empty source_id: {type(source).__name__}")
        source._plugin_id = plugin_id  # type: ignore[attr-defined]
        cls._sources[source.source_id] = source

    @classmethod
    def get(cls, source_id: str) -> Optional[MaterialSource]:
        return cls._sources.get(source_id)

    @classmethod
    def list(cls) -> list[dict[str, str]]:
        return [
            {"id": s.source_id, "name": s.source_name}
            for s in cls._sources.values()
        ]

    @classmethod
    async def search(
        cls,
        query: str,
        top_k_per_source: int = 10,
        source_ids: Optional[list[str]] = None,
    ) -> list[MaterialSearchResult]:
        """跨所有（或指定）素材源进行搜索。

        每个源独立搜索，结果合并后按分数降序排列。
        """
        if source_ids:
            sources = [s for sid, s in cls._sources.items() if sid in source_ids]
        else:
            sources = list(cls._sources.values())

        all_results: list[MaterialSearchResult] = []
        for src in sources:
            try:
                results = await src.search(query, top_k=top_k_per_source)
                for asset, score in results:
                    all_results.append(MaterialSearchResult(
                        asset=asset,
                        score=score,
                        source_name=src.source_name,
                    ))
            except Exception:
                pass  # 单源搜索失败不影响其他源

        # 按分数降序排列
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results

    @classmethod
    async def get_asset(cls, source_id: str, asset_id: str) -> MaterialAsset | None:
        """从指定源获取单个素材。"""
        src = cls._sources.get(source_id)
        if src is None:
            return None
        return await src.get_asset(asset_id)

    @classmethod
    def list_by_plugin(cls, plugin_id: str) -> list[str]:
        """列出指定插件注册的所有素材源 ID。"""
        return [
            s.source_id for s in cls._sources.values()
            if getattr(s, "_plugin_id", "") == plugin_id
        ]

    @classmethod
    def clear(cls) -> None:
        cls._sources.clear()
