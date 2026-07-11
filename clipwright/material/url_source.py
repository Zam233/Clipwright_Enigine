"""URL 素材源 — 通过 URL 直接访问远程素材。

支持两种模式：
1. 从 JSON 配置中预定义的 URL 素材列表
2. 直接通过 URL 参数访问（按需抓取元信息）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType


class UrlMaterialSource(MaterialSource):
    """通过 URL 访问远程素材的源。"""

    source_id: str = ""
    source_name: str = ""

    def __init__(
        self,
        source_id: str,
        base_url: str = "",
        catalog_path: Optional[str | Path] = None,
        source_name: str = "",
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name or source_id
        self._base_url = base_url.rstrip("/")
        self._assets: list[MaterialAsset] = []

        if catalog_path:
            path = Path(catalog_path)
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        self._add_from_dict(item)

    def _add_from_dict(self, item: dict[str, Any]) -> None:
        """从字典添加一个素材。"""
        try:
            url = item.get("url", "")
            if self._base_url and url and not url.startswith("http"):
                url = f"{self._base_url}/{url.lstrip('/')}"
            asset = MaterialAsset(
                id=item.get("id", url),
                title=item.get("title", item.get("name", "")),
                type=MaterialType(item["type"]) if "type" in item else MaterialType.VIDEO,
                url=url,
                thumbnail_url=item.get("thumbnail_url"),
                tags=item.get("tags", []),
                duration_sec=item.get("duration_sec"),
                resolution=item.get("resolution"),
                source=self.source_id,
                metadata=item.get("metadata", {}),
            )
            self._assets.append(asset)
        except (KeyError, ValueError):
            pass

    def add_url(
        self,
        url: str,
        title: str = "",
        tags: Optional[list[str]] = None,
        **metadata: Any,
    ) -> str:
        """动态添加一个 URL 素材。返回 asset_id。"""
        asset_id = f"url_{len(self._assets)}"
        full_url = f"{self._base_url}/{url.lstrip('/')}" if self._base_url and not url.startswith("http") else url
        asset = MaterialAsset(
            id=asset_id,
            title=title or full_url,
            type=MaterialType.VIDEO,
            url=full_url,
            tags=tags or [],
            source=self.source_id,
            metadata=metadata,
        )
        self._assets.append(asset)
        return asset_id

    async def search(
        self,
        query: str,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        """搜索预定义的 URL 素材。"""
        query_lower = query.lower()
        scored: list[tuple[MaterialAsset, float]] = []
        for asset in self._assets:
            score = 0.0
            if query_lower in asset.title.lower():
                score += 0.6
            for tag in asset.tags:
                if query_lower in tag.lower():
                    score += 0.3
            if score > 0:
                scored.append((asset, min(score, 1.0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    async def get_asset(self, asset_id: str) -> MaterialAsset | None:
        for a in self._assets:
            if a.id == asset_id:
                return a
        return None

    async def count(self) -> int:
        return len(self._assets)
