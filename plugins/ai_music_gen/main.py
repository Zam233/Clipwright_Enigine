"""AI 文生音乐插件 — 从情绪/风格/时长提示生成免版税 BGM。

支持提供商：
  - "suno": Suno API（需 SUNO_API_KEY）
  - "local": 本地 MusicGen（需 musicgen 包）

注册为 Tool（ai_music_generate）+ MaterialSource。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from clipwright.material.base import MaterialSource
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins import CapabilityPlugin
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class AIMusicGenTool:
    name = "ai_music_generate"
    description = "从情绪/风格描述生成免版税背景音乐"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "音乐描述（如 'upbeat corporate, 120bpm'）"},
            "duration_sec": {"type": "number", "default": 30},
            "genre": {"type": "string", "default": ""},
        },
        "required": ["prompt"],
    }

    def __init__(self, provider: str = "suno", api_key: str = "") -> None:
        self._provider = provider
        self._api_key = api_key

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        try:
            if self._provider == "suno":
                return await self._gen_suno(prompt, params)
            return {"success": False, "error": f"未知 provider: {self._provider}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _gen_suno(self, prompt: str, params: dict) -> dict:
        key = self._api_key or os.environ.get("SUNO_API_KEY", "")
        if not key:
            return {"success": False, "error": "SUNO_API_KEY 未配置"}
        duration = int(params.get("duration_sec", 30))
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post("https://api.suno.ai/v1/generate",
                headers={"Authorization": f"Bearer {key}"},
                json={"prompt": prompt, "duration": duration, "make_instrumental": True})
            resp.raise_for_status()
            data = resp.json()
            audio_url = data.get("audio_url", "")
            if audio_url:
                return {"success": True, "url": audio_url, "provider": "suno", "duration_sec": duration}
            return {"success": False, "error": "Suno 未返回音频"}


class AIMusicGenSource(MaterialSource):
    source_id: str = "ai_music_gen"
    source_name: str = "AI 文生音乐"

    def __init__(self, tool: AIMusicGenTool) -> None:
        self._tool = tool

    async def search(self, query: str, top_k: int = 1, media_type: str = "all", **kw: Any) -> list[tuple[MaterialAsset, float]]:
        if media_type not in ("audio", "all"):
            return []
        result = await self._tool.execute({"prompt": query, "duration_sec": 30})
        if not result.get("success"):
            return []
        asset = MaterialAsset(
            id=f"aimusic_{uuid.uuid4().hex[:8]}", title=query, type=MaterialType.AUDIO,
            url=result["url"], thumbnail_url="", tags=["ai_generated", query],
            duration_sec=float(result.get("duration_sec", 30)), source=self.source_id,
            metadata={"prompt": query, "provider": result.get("provider", "")},
        )
        return [(asset, 0.8)]


class AIMusicGenPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="ai_music_gen", name="AI Music Generation", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Generate royalty-free BGM from text via Suno API",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        cfg = self.config or {}
        provider = cfg.get("provider", "suno")
        tool = AIMusicGenTool(provider=provider, api_key=cfg.get("api_key", ""))
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        MaterialRegistry.register(AIMusicGenSource(tool), plugin_id=self.manifest.id)
        logger.info("[AIMusicGen] Tool + MaterialSource 已注册 (provider=%s)", provider)

    def shutdown(self) -> None:
        pass


__all__ = ["AIMusicGenPlugin"]
