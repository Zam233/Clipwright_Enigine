"""AI 文生图插件 — 从文字提示生成图片素材。

支持提供商（通过 config.yaml 的 provider 字段切换）：
  - "dalle": OpenAI DALL-E 3（需 OPENAI_API_KEY）
  - "flux": Black Forest Labs Flux（需 FLUX_API_KEY）
  - "local": 本地 Stable Diffusion / ComfyUI（需 SD_API_URL）

注册为 MaterialSource（搜索=生成）和 Tool（ai_image_generate）。
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Optional

import httpx

from clipwright.material.base import MaterialSource
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins import CapabilityPlugin
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class AIImageGenTool:
    name = "ai_image_generate"
    description = "从文字提示生成图片，返回图片 URL 或本地路径"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图片描述"},
            "width": {"type": "integer", "default": 1024},
            "height": {"type": "integer", "default": 576},
            "style": {"type": "string", "default": "natural"},
        },
        "required": ["prompt"],
    }

    def __init__(self, provider: str = "dalle", api_key: str = "", api_url: str = "") -> None:
        self._provider = provider
        self._api_key = api_key
        self._api_url = api_url

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        w = params.get("width", 1024)
        h = params.get("height", 576)
        try:
            if self._provider == "dalle":
                return await self._gen_dalle(prompt, w, h, params.get("style", "natural"))
            elif self._provider == "flux":
                return await self._gen_flux(prompt, w, h)
            elif self._provider == "local":
                return await self._gen_local(prompt, w, h)
            return {"success": False, "error": f"未知 provider: {self._provider}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _gen_dalle(self, prompt: str, w: int, h: int, style: str) -> dict:
        key = self._api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return {"success": False, "error": "OPENAI_API_KEY 未配置"}
        async with httpx.AsyncClient(timeout=60) as c:
            resp = await c.post("https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "dall-e-3", "prompt": prompt, "size": f"{w}x{h}", "style": style, "n": 1})
            resp.raise_for_status()
            url = resp.json()["data"][0]["url"]
            return {"success": True, "url": url, "provider": "dalle"}

    async def _gen_flux(self, prompt: str, w: int, h: int) -> dict:
        key = self._api_key or os.environ.get("FLUX_API_KEY", "")
        if not key:
            return {"success": False, "error": "FLUX_API_KEY 未配置"}
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post("https://api.bfl.ml/v1/flux-pro-1.1",
                headers={"x-key": key},
                json={"prompt": prompt, "width": w, "height": h})
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "url": data.get("result", {}).get("sample", ""), "provider": "flux"}

    async def _gen_local(self, prompt: str, w: int, h: int) -> dict:
        url = self._api_url or os.environ.get("SD_API_URL", "http://127.0.0.1:7860")
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post(f"{url}/sdapi/v1/txt2img",
                json={"prompt": prompt, "width": w, "height": h, "steps": 20})
            resp.raise_for_status()
            images = resp.json().get("images", [])
            if images:
                out = f"PluginData/assets/ai_gen_{uuid.uuid4().hex[:8]}.png"
                import base64
                from pathlib import Path
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_bytes(base64.b64decode(images[0]))
                return {"success": True, "url": out, "provider": "local"}
            return {"success": False, "error": "本地 SD 未返回图片"}


class AIImageGenSource(MaterialSource):
    source_id: str = "ai_image_gen"
    source_name: str = "AI 文生图"

    def __init__(self, tool: AIImageGenTool) -> None:
        self._tool = tool

    async def search(self, query: str, top_k: int = 1, media_type: str = "all", **kw: Any) -> list[tuple[MaterialAsset, float]]:
        if media_type not in ("photo", "all"):
            return []
        result = await self._tool.execute({"prompt": query, "width": 1024, "height": 576})
        if not result.get("success"):
            return []
        asset = MaterialAsset(
            id=f"aigen_{uuid.uuid4().hex[:8]}", title=query, type=MaterialType.IMAGE,
            url=result["url"], thumbnail_url=result["url"], tags=["ai_generated", query],
            resolution="1024x576", source=self.source_id,
            metadata={"prompt": query, "provider": result.get("provider", "")},
        )
        return [(asset, 0.9)]


class AIImageGenPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="ai_image_gen", name="AI Image Generation", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Generate images from text via DALL-E/Flux/local SD",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        cfg = self.config or {}
        provider = cfg.get("provider", "dalle")
        tool = AIImageGenTool(provider=provider, api_key=cfg.get("api_key", ""), api_url=cfg.get("api_url", ""))
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        MaterialRegistry.register(AIImageGenSource(tool), plugin_id=self.manifest.id)
        logger.info("[AIImageGen] Tool + MaterialSource 已注册 (provider=%s)", provider)

    def shutdown(self) -> None:
        pass


__all__ = ["AIImageGenPlugin"]
