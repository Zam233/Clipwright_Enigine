"""GIF & 贴纸素材插件 — 从 Giphy/Tenor 搜索反应 GIF 和 Meme 贴纸。

设置 GIPHY_API_KEY 或 TENOR_API_KEY 启用。
Giphy 公共 beta key: dc6zaTOxFJmzC（有限速率）。
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

class GiphySource(MaterialSource):
    source_id: str = "giphy"
    source_name: str = "Giphy GIF 库"
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("GIPHY_API_KEY", "dc6zaTOxFJmzC")
        self._client = httpx.AsyncClient(base_url="https://api.giphy.com/v1", timeout=10)

    async def search(self, query: str, top_k: int = 10, media_type: str = "all", **kw: Any) -> list[tuple[MaterialAsset, float]]:
        try:
            resp = await self._client.get("/gifs/search", params={"api_key": self._api_key, "q": query, "limit": min(top_k, 25)})
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        results = []
        for gif in data.get("data", []):
            images = gif.get("images", {})
            mp4 = images.get("fixed_height", {}).get("mp4", "")
            url = mp4 or images.get("original", {}).get("url", "")
            if not url: continue
            asset = MaterialAsset(id=f"giphy_{gif['id']}", title=gif.get("title", query), type=MaterialType.IMAGE,
                url=url, thumbnail_url=images.get("fixed_height_still", {}).get("url", ""),
                tags=[query, "gif"], source=self.source_id,
                metadata={"giphy_id": gif["id"], "rating": gif.get("rating", "")})
            results.append((asset, 0.7))
        return results

    async def close(self) -> None:
        await self._client.aclose()

class GifStickerPlugin(CapabilityPlugin):
    manifest = PluginManifest(id="gif_sticker", name="GIF & Sticker Material", version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE, description="Search GIFs and stickers from Giphy/Tenor", author="Clipwright Team")
    def __init__(self) -> None:
        self._source: Optional[GiphySource] = None
    def initialize(self) -> None:
        self._source = GiphySource()
        MaterialRegistry.register(self._source, plugin_id=self.manifest.id)
        print("[GifSticker] Giphy GIF 库已注册")
    def shutdown(self) -> None:
        if self._source:
            import asyncio
            try: asyncio.create_task(self._source.close())
            except Exception: pass

__all__ = ["GifStickerPlugin"]
