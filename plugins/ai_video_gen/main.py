"""AI 文生视频插件 — 从文字提示生成短视频片段。
默认接入火山方舟 Seedance（异步任务：创建 + 轮询，Bearer API Key 鉴权）；
保留 Kling / Runway 分支可切回。

任务成功的视频 URL 24 小时内失效，产物统一下载落地到 PluginData/assets/
（下载前经 assert_public_url 防 SSRF），返回本地路径 + 远端 URL。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.security import assert_public_url
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins.generated_source import make_generated_source, record_generated

DEFAULT_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_SEEDANCE_MODEL = "doubao-seedance-1-5-pro-251215"


def _bearer_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _poll_task_path(task_id: str) -> str:
    return "/contents/generations/tasks/{}".format(task_id)


class AIVideoGenTool(BaseTool):
    name = "ai_video_generate"
    agent_callable = True
    description = "从文字提示生成短视频（5-10 秒，支持首帧图生视频），异步执行"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "视频描述"},
            "duration_sec": {"type": "number", "default": 5},
            "aspect_ratio": {"type": "string", "default": "16:9", "description": "宽高比：16:9 / 9:16 / 1:1 / 4:3"},
            "resolution": {"type": "string", "default": "720p", "enum": ["480p", "720p", "1080p"], "description": "分辨率"},
            "image_url": {"type": "string", "description": "首帧图片 URL（可选，图生视频）"},
            "seed": {"type": "integer", "description": "随机种子（可选）"},
        },
        "required": ["prompt"],
    }

    def __init__(
        self,
        provider: str = "volcengine",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        poll_interval: float = 5.0,
        # 批5：600s < TaskQueue 900s 超时，避免付费生成在下载阶段被队列杀掉
        poll_timeout: float = 600.0,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    def is_available(self) -> bool:
        """按当前 provider 检查凭据是否配置（配置优先、env 兜底）。"""
        if self._provider == "volcengine":
            return bool(self._api_key or os.environ.get("ARK_API_KEY", ""))
        if self._provider == "kling":
            return bool(self._api_key or os.environ.get("KLING_API_KEY", ""))
        if self._provider == "runway":
            return bool(self._api_key or os.environ.get("RUNWAY_API_KEY", ""))
        return False

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        try:
            if self._provider == "volcengine":
                return await self._gen_volcengine(prompt, kwargs)
            elif self._provider == "kling":
                return await self._gen_kling(prompt, kwargs)
            elif self._provider == "runway":
                return await self._gen_runway(prompt, kwargs)
            return {"success": False, "error": f"未知 provider: {self._provider}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _gen_volcengine(self, prompt: str, params: dict) -> dict:
        key = self._api_key or os.environ.get("ARK_API_KEY", "")
        if not key:
            return {"success": False, "error": "ARK_API_KEY 未配置（或经插件配置 api_key）"}
        base = (self._base_url or os.environ.get("ARK_BASE_URL", "") or DEFAULT_ARK_BASE).rstrip("/")
        if not base.startswith("https://"):
            return {"success": False, "error": "ark_base_url 必须为 https://"}
        model = self._model or os.environ.get("ARK_SEEDANCE_MODEL", "") or DEFAULT_SEEDANCE_MODEL

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if params.get("image_url"):
            content.append({
                "type": "image_url",
                "image_url": {"url": str(params["image_url"])},
                "role": "first_frame",
            })
        payload: dict[str, Any] = {
            "model": model,
            "content": content,
            "resolution": str(params.get("resolution", "720p")),
            "ratio": str(params.get("aspect_ratio", "16:9")),
            "duration": int(params.get("duration_sec", 5)),
            "watermark": False,
        }
        if params.get("seed") is not None:
            payload["seed"] = int(params["seed"])

        headers = _bearer_headers(key)
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        task_path = "/contents/generations/tasks"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._poll_timeout
        async with httpx.AsyncClient(base_url=base, timeout=60) as c:
            resp = await c.post(task_path, headers=headers, content=body_bytes)
            resp.raise_for_status()
            task_id = str(resp.json().get("id", ""))
            if not task_id:
                return {"success": False, "error": "创建任务未返回 id"}
            while loop.time() < deadline:
                await asyncio.sleep(self._poll_interval)
                # 批5：单次轮询失败（网络/5xx）不再废弃整个付费任务
                try:
                    q = await c.get(_poll_task_path(task_id), headers=headers)
                    q.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (429, 500, 502, 503, 504):
                        continue
                    return {"success": False, "error": f"查询任务 HTTP {e.response.status_code}",
                            "task_id": task_id}
                except httpx.HTTPError:
                    continue
                tdata = q.json()
                status = str(tdata.get("status", ""))
                if status == "succeeded":
                    video_url = str((tdata.get("content") or {}).get("video_url", ""))
                    if not video_url:
                        return {"success": False, "error": "任务成功但未返回 video_url", "task_id": task_id}
                    local_path = await self._download(c, video_url)
                    record_generated("ai_video_gen", prompt=prompt, type="video", path=local_path,
                                     duration_sec=payload["duration"], resolution=payload["resolution"])
                    return {
                        "success": True, "url": local_path, "path": local_path,
                        "remote_url": video_url, "task_id": task_id,
                        "model": model, "provider": "volcengine",
                    }
                if status in ("failed", "cancelled", "expired"):
                    err = tdata.get("error") or {}
                    msg = " ".join(str(err.get(k, "")) for k in ("code", "message")).strip()
                    return {"success": False, "error": msg or ("任务状态: " + status), "task_id": task_id}
            return {"success": False, "error": "生成超时", "task_id": task_id}

    @staticmethod
    async def _download(client: httpx.AsyncClient, video_url: str) -> str:
        """下载生成视频到 PluginData/assets/（URL 24h 失效；先做 SSRF 校验）。"""
        assert_public_url(video_url)
        r = await client.get(video_url)
        r.raise_for_status()
        out_dir = Path("PluginData/assets")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "ai_vid_{}.mp4".format(uuid.uuid4().hex[:12])
        out.write_bytes(r.content)
        return str(out)

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
                    if not video_url:
                        return {"success": False, "error": "生成成功但未返回视频 URL", "task_id": task_id}
                    # 批5：统一落地 + 登记（URL 有效期短，且需进入素材检索链路）
                    local_path = await self._download(c, video_url)
                    record_generated("ai_video_gen", prompt=prompt, type="video",
                                     path=local_path,
                                     duration_sec=int(params.get("duration_sec", 5)))
                    return {"success": True, "url": local_path, "path": local_path,
                            "remote_url": video_url, "provider": "kling", "task_id": task_id}
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
                    video_url = data.get("output", [None])[0]
                    if not video_url:
                        return {"success": False, "error": "生成成功但未返回视频 URL"}
                    local_path = await self._download(c, video_url)
                    record_generated("ai_video_gen", prompt=prompt, type="video",
                                     path=local_path,
                                     duration_sec=int(params.get("duration_sec", 5)))
                    return {"success": True, "url": local_path, "path": local_path,
                            "remote_url": video_url, "provider": "runway"}
                if data.get("status") == "FAILED":
                    return {"success": False, "error": data.get("failure", "生成失败")}
            return {"success": False, "error": "生成超时"}


class AIVideoGenPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="ai_video_gen", name="AI Video Generation", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="Generate video clips from text via Volcengine Seedance (default) / Kling / Runway",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        cfg = self.config or {}
        tool = AIVideoGenTool(
            provider=cfg.get("provider", "volcengine"),
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("ark_base_url", ""),
            model=cfg.get("model", ""),
            poll_interval=float(cfg.get("poll_interval_sec", 5)),
            poll_timeout=float(cfg.get("poll_timeout_sec", 900)),
        )
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        MaterialRegistry.register(
            make_generated_source("ai_video_gen", "AI 生成视频", "video"),
            plugin_id=self.manifest.id)
        logger.info("[AIVideoGen] Tool + MaterialSource 已注册 (provider=%s)", tool._provider)

    def shutdown(self) -> None:
        # 批7：disable 时真正移除工具与素材源（旧实现 pass → 禁用后
        # 工具仍可用、能力概览仍播报）
        try:
            ToolRegistry.unregister("ai_video_generate")
        except Exception:
            pass
        try:
            from clipwright.material.registry import MaterialRegistry
            MaterialRegistry.unregister("ai_video_gen")
        except Exception:
            pass


__all__ = ["AIVideoGenPlugin"]
