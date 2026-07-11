"""JSON 目录素材源 — 从 JSON 文件中读取素材列表。

JSON 格式（每行一个或整个数组）：
```json
[
  {
    "id": "vid_001",
    "title": "Nature Background",
    "type": "video",
    "url": "https://cdn.example.com/vid.mp4",
    "local_path": "materials/vid_001.mp4",
    "tags": ["nature", "background"],
    "duration_sec": 30,
    "resolution": "1920x1080"
  }
]
```
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType


class JsonCatalogSource(MaterialSource):
    """从 JSON 文件加载的素材目录。"""

    source_id: str = ""
    source_name: str = ""

    def __init__(
        self,
        source_id: str,
        catalog_path: str | Path,
        source_name: str = "",
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name or source_id
        self._catalog_path = Path(catalog_path)
        self._assets: list[MaterialAsset] = []
        self._load()

    def _load(self) -> None:
        """从 JSON 文件加载素材列表。"""
        if not self._catalog_path.exists():
            return
        raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("assets", raw.get("materials", []))
        for item in raw if isinstance(raw, list) else []:
            try:
                asset = MaterialAsset(
                    id=item.get("id", ""),
                    title=item.get("title", item.get("name", "")),
                    type=MaterialType(item["type"]) if "type" in item else MaterialType.VIDEO,
                    url=item.get("url"),
                    local_path=item.get("local_path"),
                    thumbnail_url=item.get("thumbnail_url"),
                    tags=item.get("tags", []),
                    duration_sec=item.get("duration_sec") or item.get("duration"),
                    file_size_bytes=item.get("file_size_bytes"),
                    resolution=item.get("resolution"),
                    source=self.source_id,
                    metadata=item.get("metadata", {}),
                )
                self._assets.append(asset)
            except (KeyError, ValueError):
                continue

    async def search(
        self,
        query: str,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        """关键词标签匹配搜索。"""
        query_lower = query.lower()
        query_terms = query_lower.split()

        scored: list[tuple[MaterialAsset, float]] = []
        for asset in self._assets:
            score = self._match(asset, query_terms)
            if score > 0:
                scored.append((asset, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    async def get_asset(self, asset_id: str) -> MaterialAsset | None:
        for a in self._assets:
            if a.id == asset_id:
                return a
        return None

    async def count(self) -> int:
        return len(self._assets)

    async def list_all(self) -> list[MaterialAsset]:
        return list(self._assets)

    @staticmethod
    def _match(asset: MaterialAsset, query_terms: list[str]) -> float:
        """计算素材与查询词的相关度分数。"""
        score = 0.0
        text_pool = (
            [asset.title.lower()]
            + [t.lower() for t in asset.tags]
            + [str(v).lower() for v in asset.metadata.values() if isinstance(v, str)]
        )

        for term in query_terms:
            for text in text_pool:
                if term in text:
                    score += 0.3
                if text == term:
                    score += 0.2  # 精确匹配加分

        # 标题匹配权重更高
        if any(term in asset.title.lower() for term in query_terms):
            score += 0.4

        return min(score, 1.0)
