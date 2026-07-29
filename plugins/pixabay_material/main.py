"""Pixabay 素材库插件 — 从 Pixabay 免费图库搜索素材。

使用前需要设置环境变量：
    PIXABAY_API_KEY=your_api_key_here

获取 API Key：https://pixabay.com/api/docs/
免费版每日 10,000 次请求。
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

PIXABAY_BASE = "https://pixabay.com/api"


class PixabayMaterialSource(MaterialSource):
    """从 Pixabay API 搜索视频和图片素材。"""

    source_id: str = "pixabay"
    source_name: str = "Pixabay 图库"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("PIXABAY_API_KEY", "")
        self._client = httpx.AsyncClient(base_url=PIXABAY_BASE, timeout=15)

    async def search(
        self, query: str, top_k: int = 10, media_type: str = "all", **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        if not self._api_key:
            return []
        results: list[tuple[MaterialAsset, float]] = []
        if media_type in ("video", "all"):
            try:
                results.extend(await self._search_videos(query, top_k))
            except Exception:
                pass
        if media_type in ("photo", "all"):
            try:
                results.extend(await self._search_images(query, top_k))
            except Exception:
                pass
        return results

    async def _search_videos(self, query: str, top_k: int) -> list[tuple[MaterialAsset, float]]:
        resp = await self._client.get("/videos/", params={
            "key": self._api_key, "q": query, "per_page": min(top_k, 200),
        })
        resp.raise_for_status()
        data = resp.json()
        results: list[tuple[MaterialAsset, float]] = []
        for hit in data.get("hits", []):
            videos = hit.get("videos", {})
            best = videos.get("large") or videos.get("medium") or videos.get("small") or videos.get("tiny")
            if not best:
                continue
            asset = MaterialAsset(
                id=f"pixabay_v_{hit['id']}",
                title=hit.get("tags", f"Pixabay Video {hit['id']}"),
                type=MaterialType.VIDEO,
                url=best["url"],
                thumbnail_url=f"https://i.vimeocdn.com/video/{hit.get('picture_id', '')}_295x166.jpg",
                tags=hit.get("tags", "").split(", "),
                duration_sec=float(hit.get("duration", 0)),
                resolution=f"{best.get('width', 0)}x{best.get('height', 0)}",
                source=self.source_id,
                metadata={"pixabay_id": hit["id"], "user": hit.get("user", ""), "pageURL": hit.get("pageURL", "")},
            )
            results.append((asset, 0.8))
        return results

    async def _search_images(self, query: str, top_k: int) -> list[tuple[MaterialAsset, float]]:
        resp = await self._client.get("/", params={
            "key": self._api_key, "q": query, "per_page": min(top_k, 200), "image_type": "photo",
        })
        resp.raise_for_status()
        data = resp.json()
        results: list[tuple[MaterialAsset, float]] = []
        for hit in data.get("hits", []):
            asset = MaterialAsset(
                id=f"pixabay_p_{hit['id']}",
                title=hit.get("tags", f"Pixabay Image {hit['id']}"),
                type=MaterialType.IMAGE,
                url=hit.get("largeImageURL", ""),
                thumbnail_url=hit.get("webformatURL", ""),
                tags=hit.get("tags", "").split(", "),
                resolution=f"{hit.get('imageWidth', 0)}x{hit.get('imageHeight', 0)}",
                source=self.source_id,
                metadata={"pixabay_id": hit["id"], "user": hit.get("user", ""), "pageURL": hit.get("pageURL", "")},
            )
            results.append((asset, 0.7))
        return results

    async def close(self) -> None:
        await self._client.aclose()


class PixabayMaterialPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="pixabay_material", name="Pixabay Material Library", version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="Search free stock videos & photos from Pixabay",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._source: Optional[PixabayMaterialSource] = None

    def initialize(self) -> None:
        api_key = (self.config or {}).get("api_key", "") or os.environ.get("PIXABAY_API_KEY", "")
        if not api_key:
            print(f"[PixabayPlugin] 跳过: 请配置 PIXABAY_API_KEY")
            return
        self._source = PixabayMaterialSource(api_key=api_key)
        MaterialRegistry.register(self._source, plugin_id=self.manifest.id)
        print(f"[PixabayPlugin] Pixabay 素材库已注册")

    def shutdown(self) -> None:
        if self._source:
            import asyncio
            try:
                asyncio.create_task(self._source.close())
            except Exception:
                pass


__all__ = ["PixabayMaterialPlugin"]
