"""Unsplash 素材库插件 — 从 Unsplash 高质量图库搜索图片素材。

使用前需要设置环境变量：
    UNSPLASH_ACCESS_KEY=your_access_key_here

获取 API Key：https://unsplash.com/developers
Demo 版每小时 50 次请求，Production 版每小时 5000 次。
注意：Unsplash 要求署名（attribution），metadata 中已包含所需链接。
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from clipwright.config import logger
from clipwright.material.base import MaterialSource
from clipwright.material.base import close_source_sync
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.schema.plugin import PluginManifest, PluginKind

UNSPLASH_BASE = "https://api.unsplash.com"


class UnsplashMaterialSource(MaterialSource):
    source_id: str = "unsplash"
    source_name: str = "Unsplash 图库"

    def __init__(self, access_key: str = "") -> None:
        self._access_key = access_key or os.environ.get("UNSPLASH_ACCESS_KEY", "")
        self._client = httpx.AsyncClient(
            base_url=UNSPLASH_BASE,
            headers={"Authorization": f"Client-ID {self._access_key}"},
            timeout=15,
        )

    async def search(
        self, query: str, top_k: int = 10, media_type: str = "all", **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        if not self._access_key or media_type == "video":
            return []
        try:
            resp = await self._client.get("/search/photos", params={
                "query": query, "per_page": min(top_k, 30), "orientation": "landscape",
            })
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: list[tuple[MaterialAsset, float]] = []
        for photo in data.get("results", []):
            urls = photo.get("urls", {})
            user = photo.get("user", {})
            asset = MaterialAsset(
                id=f"unsplash_{photo['id']}",
                title=photo.get("alt_description") or photo.get("description") or f"Unsplash {photo['id']}",
                type=MaterialType.IMAGE,
                url=urls.get("raw", ""),
                thumbnail_url=urls.get("small", ""),
                tags=[query],
                resolution=f"{photo.get('width', 0)}x{photo.get('height', 0)}",
                source=self.source_id,
                # 轮 82（版权链）：Unsplash License 免费商用
                author=user.get("name", ""),
                source_url=photo.get("links", {}).get("html", ""),
                license="Unsplash License",
                license_url="https://unsplash.com/license",
                metadata={
                    "unsplash_id": photo["id"],
                    "photographer": user.get("name", ""),
                    "photographer_url": user.get("links", {}).get("html", ""),
                    "unsplash_url": photo.get("links", {}).get("html", ""),
                    "color": photo.get("color", ""),
                    "likes": photo.get("likes", 0),
                    "attribution": f"Photo by {user.get('name', '')} on Unsplash",
                },
            )
            results.append((asset, 0.85))
        return results

    async def close(self) -> None:
        await self._client.aclose()


class UnsplashMaterialPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="unsplash_material", name="Unsplash Material Library", version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="Search high-quality free photos from Unsplash",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._source: Optional[UnsplashMaterialSource] = None

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：配好 access_key 才贡献素材源。"""
        key = (self.config or {}).get("access_key", "") or os.environ.get("UNSPLASH_ACCESS_KEY", "")
        if not key:
            logger.info("[UnsplashPlugin] 未配置 access_key（config.yaml 或 "
                        "UNSPLASH_ACCESS_KEY）——本次不贡献素材源")
            return []
        self._source = UnsplashMaterialSource(access_key=key)
        return [Contribution(
            ContributionKind.MATERIAL_SOURCE, self._source.source_id, self._source,
        )]

    def shutdown(self) -> None:
        # A3：同步安全关闭（原 create_task 永不 await）
        if self._source:
            close_source_sync(self._source)


__all__ = ["UnsplashMaterialPlugin"]
