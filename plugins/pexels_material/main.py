"""Pexels 素材库插件 — 从 Pexels 免费图库搜索素材。

使用前需要设置环境变量：
    PEXELS_API_KEY=your_api_key_here

获取 API Key：https://www.pexels.com/api/
免费版每小时 200 次请求。
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

PEXELS_BASE = "https://api.pexels.com"

# ── Pexels Material Source ─────────────────────────────

class PexelsMaterialSource(MaterialSource):
    """从 Pexels API 搜索视频和图片素材。"""

    source_id: str = "pexels"
    source_name: str = "Pexels 图库"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("PEXELS_API_KEY", "")
        self._client = httpx.AsyncClient(
            base_url=PEXELS_BASE,
            headers={"Authorization": self._api_key},
            timeout=15,
        )

    async def search(
        self,
        query: str,
        top_k: int = 10,
        media_type: str = "all",
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        """搜索 Pexels 视频和图片。

        Args:
            query: 搜索关键词
            top_k: 每类返回数量
            media_type: "video" / "photo" / "all"
        """
        if not self._api_key:
            import logging
            logging.getLogger("pexels").warning("Pexels API Key 未配置，跳过搜索")
            return []

        results: list[tuple[MaterialAsset, float]] = []

        import logging
        log = logging.getLogger("pexels")
        if media_type in ("video", "all"):
            try:
                videos = await self._search_videos(query, top_k)
                results.extend(videos)
                if videos:
                    log.info("Pexels videos found: %d for '%s'", len(videos), query)
                else:
                    log.debug("No Pexels videos for '%s'", query)
            except Exception as e:
                log.warning("Pexels视频搜索失败: %s", e)

        if media_type in ("photo", "all"):
            try:
                photos = await self._search_photos(query, top_k)
                results.extend(photos)
                if photos:
                    log.info("Pexels photos found: %d for '%s'", len(photos), query)
            except Exception as e:
                log.warning("Pexels图片搜索失败: %s", e)

        return results

    async def _search_videos(
        self, query: str, top_k: int
    ) -> list[tuple[MaterialAsset, float]]:
        """搜索 Pexels 视频。"""
        resp = await self._client.get("/videos/search", params={
            "query": query, "per_page": min(top_k, 80), "size": "medium",
        })
        # 轮 81：429 单次退避重试（免费额度 200 次/小时，限流是常态而非异常）
        if resp.status_code == 429:
            import asyncio as _aio
            await _aio.sleep(2.0)
            resp = await self._client.get("/videos/search", params={
                "query": query, "per_page": min(top_k, 80), "size": "medium",
            })
        resp.raise_for_status()
        data = resp.json()

        results: list[tuple[MaterialAsset, float]] = []
        for video in data.get("videos", []):
            files = video.get("video_files", [])
            # 选最高质量的文件
            best_file = self._pick_best_file(files)
            if not best_file:
                continue

            asset = MaterialAsset(
                id=f"pexels_v_{video['id']}",
                title=video.get("alt", "") or f"Pexels Video {video['id']}",
                type=MaterialType.VIDEO,
                url=best_file["link"],
                thumbnail_url=video.get("image", ""),
                tags=video.get("tags", [f"pexels_{query}"]),
                duration_sec=float(video.get("duration", 0)),
                resolution=f"{video.get('width', 0)}x{video.get('height', 0)}",
                source=self.source_id,
                # 轮 82（版权链）：Pexels License 免费商用但需尊重署名惯例
                author=video.get("user", {}).get("name", ""),
                source_url=video.get("url", ""),
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                metadata={
                    "pexels_id": video["id"],
                    "photographer": video.get("user", {}).get("name", ""),
                    "photographer_url": video.get("user", {}).get("url", ""),
                    "pexels_url": video.get("url", ""),
                    "file_size": best_file.get("size", 0),
                    "file_type": best_file.get("file_type", ""),
                    "fps": best_file.get("fps", 30),
                },
            )
            results.append((asset, 0.8))

        return results

    async def _search_photos(
        self, query: str, top_k: int
    ) -> list[tuple[MaterialAsset, float]]:
        """搜索 Pexels 图片。"""
        resp = await self._client.get("/v1/search", params={
            "query": query, "per_page": min(top_k, 80),
        })
        # 轮 81：429 单次退避重试（与视频搜索同策略）
        if resp.status_code == 429:
            import asyncio as _aio
            await _aio.sleep(2.0)
            resp = await self._client.get("/v1/search", params={
                "query": query, "per_page": min(top_k, 80),
            })
        resp.raise_for_status()
        data = resp.json()

        results: list[tuple[MaterialAsset, float]] = []
        for photo in data.get("photos", []):
            src = photo.get("src", {})
            asset = MaterialAsset(
                id=f"pexels_p_{photo['id']}",
                title=photo.get("alt", "") or f"Pexels Photo {photo['id']}",
                type=MaterialType.IMAGE,
                url=src.get("original", ""),
                thumbnail_url=src.get("medium", "") or src.get("small", ""),
                tags=[f"pexels_{query}"],
                resolution=f"{photo.get('width', 0)}x{photo.get('height', 0)}",
                source=self.source_id,
                # 轮 82（版权链）
                author=photo.get("photographer", ""),
                source_url=photo.get("url", ""),
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                metadata={
                    "pexels_id": photo["id"],
                    "photographer": photo.get("photographer", ""),
                    "photographer_url": photo.get("photographer_url", ""),
                    "pexels_url": photo.get("url", ""),
                    "avg_color": photo.get("avg_color", ""),
                },
            )
            results.append((asset, 0.7))

        return results

    @staticmethod
    def _pick_best_file(files: list[dict]) -> Optional[dict]:
        """从多个视频文件中选最高质量的一个。"""
        if not files:
            return None
        # 优先 hd 质量，其次 sd
        priority = {"hd": 2, "sd": 1}
        scored = [
            (f, priority.get(f.get("quality"), 0))
            for f in files
            if f.get("link")
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored else files[0]

    async def close(self) -> None:
        await self._client.aclose()


# ── Plugin ────────────────────────────────────────────

class PexelsMaterialPlugin(CapabilityPlugin):
    """把 Pexels 素材源作为 material_source 贡献出去（P1 新契约）。"""

    manifest = PluginManifest(
        id="pexels_material",
        name="Pexels Material Library",
        version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="Search and import free stock videos & photos from Pexels",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._source: Optional[PexelsMaterialSource] = None

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：配好 api_key 才贡献素材源（未配置即零贡献）。"""
        # 优先使用 config.yaml 中的 api_key，其次环境变量
        api_key = (self.config or {}).get("api_key", "") or os.environ.get("PEXELS_API_KEY", "")
        if not api_key:
            # B4：迁移前这里有一行 print 提示；改成结构化日志，用户仍能查到原因
            logger.info("[PexelsPlugin] 未配置 api_key（config.yaml 或 PEXELS_API_KEY）"
                        "——本次不贡献素材源")
            return []
        self._source = PexelsMaterialSource(api_key=api_key)
        return [Contribution(
            ContributionKind.MATERIAL_SOURCE, self._source.source_id, self._source,
        )]

    def shutdown(self) -> None:
        # A3：同步安全关闭（原 create_task 永不 await）
        if self._source:
            close_source_sync(self._source)


__all__ = ["PexelsMaterialPlugin"]
