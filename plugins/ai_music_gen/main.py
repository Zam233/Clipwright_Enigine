"""AI 文生音乐插件 — 从情绪/风格描述生成免版税 BGM。
支持 Suno API。生成结果通过 ToolRegistry 调用。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class AIMusicGenTool(BaseTool):
    name = "ai_music_generate"
    agent_callable = True
    description = "从情绪/风格描述生成免版税背景音乐"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "音乐描述"},
            "duration_sec": {"type": "number", "default": 30},
            "genre": {"type": "string", "default": ""},
        },
        "required": ["prompt"],
    }

    def __init__(self, provider: str = "suno", api_key: str = "") -> None:
        self._provider = provider
        self._api_key = api_key

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        try:
            if self._provider == "suno":
                return await self._gen_suno(prompt, kwargs)
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
        logger.info("[AIMusicGen] Tool 已注册 (provider=%s)", provider)

    def shutdown(self) -> None:
        pass


__all__ = ["AIMusicGenPlugin"]
