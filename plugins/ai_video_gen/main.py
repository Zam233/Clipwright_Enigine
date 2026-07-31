"""AI 文生视频插件 — 从文字提示生成短视频片段。
支持 Kling / Runway API。生成结果通过 ToolRegistry 调用。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class AIVideoGenTool(BaseTool):
    name = "ai_video_generate"
    agent_callable = True
    description = "从文字提示生成短视频（5-10 秒），异步执行"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "视频描述"},
            "duration_sec": {"type": "number", "default": 5},
            "aspect_ratio": {"type": "string", "default": "16:9"},
        },
        "required": ["prompt"],
    }

    def __init__(self, provider: str = "kling", api_key: str = "") -> None:
        self._provider = provider
        self._api_key = api_key

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        try:
            if self._provider == "kling":
                return await self._gen_kling(prompt, kwargs)
            elif self._provider == "runway":
                return await self._gen_runway(prompt, kwargs)
            return {"success": False, "error": f"未知 provider: {self._provider}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _gen_kling(self, prompt: str, params: dict) -> dict:
        key = self._api_key or os.environ.get("KLING_API_KEY", "")
        if not key:
            return {"success": False, "error": "KLING_API_KEY 未配置"}
        async with httpx.AsyncClient(timeout=180) as c:
            resp = await c.post("https://api.klingai.com/v1/videos/text2video",
                headers={"Authorization": f"Bearer {key}"},
                json={"prompt": prompt, "duration": str(int(params.get("duration_sec", 5))),
                       "aspect_ratio": params.get("aspect_ratio", "16:9")})
            resp.raise_for_status()
            task_id = resp.json().get("data", {}).get("task_id", "")
            if not task_id:
                return {"success": False, "error": "未返回 task_id"}
            for _ in range(60):
                await asyncio.sleep(3)
                status_resp = await c.get(f"https://api.klingai.com/v1/videos/text2video/{task_id}",
                    headers={"Authorization": f"Bearer {key}"})
                data = status_resp.json().get("data", {})
                if data.get("task_status") == "succeed":
                    video_url = data.get("task_result", {}).get("videos", [{}])[0].get("url", "")
                    return {"success": True, "url": video_url, "provider": "kling", "task_id": task_id}
                if data.get("task_status") == "failed":
                    return {"success": False, "error": data.get("task_status_msg", "生成失败")}
            return {"success": False, "error": "生成超时"}

    async def _gen_runway(self, prompt: str, params: dict) -> dict:
        key = self._api_key or os.environ.get("RUNWAY_API_KEY", "")
        if not key:
            return {"success": False, "error": "RUNWAY_API_KEY 未配置"}
        async with httpx.AsyncClient(timeout=180) as c:
            resp = await c.post("https://api.dev.runwayml.com/v1/text_to_video",
                headers={"Authorization": f"Bearer {key}", "X-Runway-Version": "2024-11-06"},
                json={"promptText": prompt, "duration": int(params.get("duration_sec", 5))})
            resp.raise_for_status()
            task_id = resp.json().get("id", "")
            for _ in range(60):
                await asyncio.sleep(3)
                status_resp = await c.get(f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {key}", "X-Runway-Version": "2024-11-06"})
                data = status_resp.json()
                if data.get("status") == "SUCCEEDED":
                    return {"success": True, "url": data.get("output", [None])[0], "provider": "runway"}
                if data.get("status") == "FAILED":
                    return {"success": False, "error": data.get("failure", "生成失败")}
            return {"success": False, "error": "生成超时"}


class AIVideoGenPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="ai_video_gen", name="AI Video Generation", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Generate video clips from text via Kling/Runway API",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        cfg = self.config or {}
        provider = cfg.get("provider", "kling")
        tool = AIVideoGenTool(provider=provider, api_key=cfg.get("api_key", ""))
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        logger.info("[AIVideoGen] Tool 已注册 (provider=%s)", provider)

    def shutdown(self) -> None:
        pass


__all__ = ["AIVideoGenPlugin"]
