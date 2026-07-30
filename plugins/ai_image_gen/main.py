"""AI 文生图插件 — 从文字提示生成图片。
支持 DALL-E / Flux / 本地 Stable Diffusion。
生成结果通过 ToolRegistry 调用，不注册为 MaterialSource（生成≠搜索）。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class AIImageGenTool(BaseTool):
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

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        w = kwargs.get("width", 1024)
        h = kwargs.get("height", 576)
        try:
            if self._provider == "dalle":
                return await self._gen_dalle(prompt, w, h, kwargs.get("style", "natural"))
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
        logger.info("[AIImageGen] Tool 已注册 (provider=%s)", provider)

    def shutdown(self) -> None:
        pass


__all__ = ["AIImageGenPlugin"]
