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

from clipwright.material.base import MaterialSource
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins import CapabilityPlugin
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

    def initialize(self) -> None:
        key = (self.config or {}).get("access_key", "") or os.environ.get("UNSPLASH_ACCESS_KEY", "")
        if not key:
            print("[UnsplashPlugin] 跳过: 请配置 UNSPLASH_ACCESS_KEY")
            return
        self._source = UnsplashMaterialSource(access_key=key)
        MaterialRegistry.register(self._source, plugin_id=self.manifest.id)
        print("[UnsplashPlugin] Unsplash 图库已注册")

    def shutdown(self) -> None:
        if self._source:
            import asyncio
            try:
                asyncio.create_task(self._source.close())
            except Exception:
                pass


__all__ = ["UnsplashMaterialPlugin"]
