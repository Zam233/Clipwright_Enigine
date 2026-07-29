"""Coverr 素材库插件 — 从 Coverr.co 精选免费视频库搜索 B-roll。

Coverr 提供编辑级精选免费视频，按场景/情绪分类，
与 MaterialAgent 的语义搜索天然契合。
无需 API Key，直接通过公开 API 搜索。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from clipwright.material.base import MaterialSource
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins import CapabilityPlugin
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.schema.plugin import PluginManifest, PluginKind

COVERR_BASE = "https://coverr.co/api"


class CoverrMaterialSource(MaterialSource):
    source_id: str = "coverr"
    source_name: str = "Coverr 精选视频"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=COVERR_BASE, timeout=15)

    async def search(
        self, query: str, top_k: int = 10, media_type: str = "all", **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        if media_type == "photo":
            return []
        try:
            resp = await self._client.get("/videos", params={"query": query, "limit": min(top_k, 30)})
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: list[tuple[MaterialAsset, float]] = []
        for video in data if isinstance(data, list) else data.get("videos", []):
            vid_id = video.get("id", "")
            asset = MaterialAsset(
                id=f"coverr_{vid_id}",
                title=video.get("title", f"Coverr {vid_id}"),
                type=MaterialType.VIDEO,
                url=video.get("url", "") or video.get("download_url", ""),
                thumbnail_url=video.get("thumbnail", "") or video.get("poster", ""),
                tags=video.get("tags", [query]),
                duration_sec=float(video.get("duration", 0)),
                resolution=video.get("resolution", ""),
                source=self.source_id,
                metadata={"coverr_id": vid_id, "category": video.get("category", "")},
            )
            results.append((asset, 0.82))
        return results

    async def close(self) -> None:
        await self._client.aclose()


class CoverrMaterialPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="coverr_material", name="Coverr Material Library", version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="Curated free stock video from Coverr.co",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._source: Optional[CoverrMaterialSource] = None

    def initialize(self) -> None:
        self._source = CoverrMaterialSource()
        MaterialRegistry.register(self._source, plugin_id=self.manifest.id)
        print("[CoverrPlugin] Coverr 精选视频库已注册")

    def shutdown(self) -> None:
        if self._source:
            import asyncio
            try:
                asyncio.create_task(self._source.close())
            except Exception:
                pass


__all__ = ["CoverrMaterialPlugin"]
