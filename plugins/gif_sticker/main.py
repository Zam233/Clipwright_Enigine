"""GIF & 贴纸素材插件 — 从 Giphy/Tenor 搜索反应 GIF 和 Meme 贴纸。

设置 GIPHY_API_KEY 或 TENOR_API_KEY 启用。
Giphy 公共 beta key: dc6zaTOxFJmzC（有限速率）。
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

class GiphySource(MaterialSource):
    source_id: str = "giphy"
    source_name: str = "Giphy GIF 库"
    def __init__(self, api_key: str = "") -> None:
        # C4：不再回退到源码里硬编码的 Giphy 公共 beta key（全用户共享限流额度，
        # 且属明文凭据）；未配置时由 contributions() 给出可读提示并零贡献
        self._api_key = api_key or os.environ.get("GIPHY_API_KEY", "")
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
    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：Giphy GIF 素材源（需 api_key / GIPHY_API_KEY）。"""
        cfg = self.config or {}
        api_key = cfg.get("api_key", "") or os.environ.get("GIPHY_API_KEY", "")
        if not api_key:
            logger.info("[GifSticker] 未配置 api_key（config.yaml 或 GIPHY_API_KEY）"
                        "——本次不贡献素材源")
            return []
        self._source = GiphySource(api_key=api_key)
        return [Contribution(
            ContributionKind.MATERIAL_SOURCE, self._source.source_id, self._source,
        )]
    def shutdown(self) -> None:
        # A3：同步安全关闭（原 create_task 永不 await）
        if self._source:
            close_source_sync(self._source)

__all__ = ["GifStickerPlugin"]
