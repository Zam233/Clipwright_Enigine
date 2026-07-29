"""BGM 素材库插件 — 背景音乐和音效搜索。

支持两种来源：
  1. Freesound.org API（需 OAuth2 token，设置 FREESOUND_API_KEY）
  2. 本地音乐目录（设置 BGM_LOCAL_DIR 环境变量）

AudioAgent 的 bgm_slots 系统可直接消费本插件的搜索结果。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import httpx

from clipwright.material.base import MaterialSource
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins import CapabilityPlugin
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.schema.plugin import PluginManifest, PluginKind

FREESOUND_BASE = "https://freesound.org/apiv2"


class FreesoundBGMSource(MaterialSource):
    """从 Freesound.org 搜索背景音乐和音效。"""

    source_id: str = "freesound"
    source_name: str = "Freesound 音乐库"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("FREESOUND_API_KEY", "")
        self._client = httpx.AsyncClient(base_url=FREESOUND_BASE, timeout=15)

    async def search(
        self, query: str, top_k: int = 10, media_type: str = "all", **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        if not self._api_key or media_type not in ("audio", "all"):
            return []
        try:
            resp = await self._client.get("/search/text/", params={
                "token": self._api_key, "query": query,
                "filter": "duration:[5 TO 300]",
                "fields": "id,name,tags,previews,duration,avg_rating",
                "page_size": min(top_k, 150),
            })
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: list[tuple[MaterialAsset, float]] = []
        for sound in data.get("results", []):
            previews = sound.get("previews", {})
            url = previews.get("preview-hq-mp3", "") or previews.get("preview-lq-mp3", "")
            if not url:
                continue
            rating = float(sound.get("avg_rating", 0)) / 5.0
            asset = MaterialAsset(
                id=f"freesound_{sound['id']}",
                title=sound.get("name", f"Freesound {sound['id']}"),
                type=MaterialType.AUDIO,
                url=url,
                thumbnail_url="",
                tags=sound.get("tags", [query]),
                duration_sec=float(sound.get("duration", 0)),
                source=self.source_id,
                metadata={"freesound_id": sound["id"], "rating": sound.get("avg_rating", 0)},
            )
            results.append((asset, 0.5 + rating * 0.4))
        return results

    async def close(self) -> None:
        await self._client.aclose()


class LocalBGMSource(MaterialSource):
    """从本地目录扫描音乐文件。"""

    source_id: str = "local_bgm"
    source_name: str = "本地音乐库"

    def __init__(self, music_dir: str = "") -> None:
        self._dir = Path(music_dir or os.environ.get("BGM_LOCAL_DIR", ""))

    async def search(
        self, query: str, top_k: int = 10, media_type: str = "all", **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        if not self._dir.exists() or media_type not in ("audio", "all"):
            return []
        exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
        results: list[tuple[MaterialAsset, float]] = []
        for f in sorted(self._dir.rglob("*")):
            if f.suffix.lower() not in exts:
                continue
            if query.lower() not in f.stem.lower() and query.lower() not in str(f.parent).lower():
                continue
            asset = MaterialAsset(
                id=f"local_{f.stem}",
                title=f.stem,
                type=MaterialType.AUDIO,
                url=str(f),
                thumbnail_url="",
                tags=[query, f.parent.name],
                duration_sec=0,
                source=self.source_id,
                metadata={"path": str(f), "format": f.suffix},
            )
            results.append((asset, 0.6))
            if len(results) >= top_k:
                break
        return results


class BGMLibraryPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="bgm_library", name="BGM Library", version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="Background music search via Freesound API and local directories",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._sources: list[MaterialSource] = []

    def initialize(self) -> None:
        api_key = (self.config or {}).get("freesound_api_key", "") or os.environ.get("FREESOUND_API_KEY", "")
        if api_key:
            src = FreesoundBGMSource(api_key=api_key)
            MaterialRegistry.register(src, plugin_id=self.manifest.id)
            self._sources.append(src)
            print("[BGMPlugin] Freesound 音乐库已注册")

        local_dir = (self.config or {}).get("local_dir", "") or os.environ.get("BGM_LOCAL_DIR", "")
        if local_dir:
            src = LocalBGMSource(music_dir=local_dir)
            MaterialRegistry.register(src, plugin_id=self.manifest.id)
            self._sources.append(src)
            print(f"[BGMPlugin] 本地音乐库已注册: {local_dir}")

        if not self._sources:
            print("[BGMPlugin] 跳过: 请配置 FREESOUND_API_KEY 或 BGM_LOCAL_DIR")

    def shutdown(self) -> None:
        import asyncio
        for src in self._sources:
            if hasattr(src, 'close'):
                try:
                    asyncio.create_task(src.close())
                except Exception:
                    pass


__all__ = ["BGMLibraryPlugin"]
