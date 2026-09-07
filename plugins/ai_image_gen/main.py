"""AI 文生图插件 — 从文字提示生成图片。
默认接入火山方舟 Seedream（`POST /api/v3/images/generations`，Bearer API Key 鉴权）；
保留 DALL-E / Flux / 本地 Stable Diffusion 分支可切回。

Seedream 生成的图片 URL 24 小时内失效，因此产物统一下载落地到
PluginData/assets/（下载前经 assert_public_url 防 SSRF），返回本地路径 + 远端 URL。
"""

from __future__ import annotations

import base64
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
DEFAULT_SEEDREAM_MODEL = "doubao-seedream-4-0-250828"

# Seedream（4.0/5.0 pro）size 直传像素时的总像素合法区间；其它模型区间不同，
# 需要精确控制时可用 size 参数直传官方档位（1K/2K/4K）或像素串绕开本换算。
_MIN_TOTAL_PX = 921_600      # 1280x720
_MAX_TOTAL_PX = 4_624_220    # 2048x2048x1.1025


def _snap_size(w: int, h: int) -> str:
    """把任意宽高按原比例收敛到 Seedream 总像素合法区间，返回 'WxH'。

    例：1024x576（58.9 万像素，低于下限）→ 1280x720；4096x4096（超上限）→ 等比缩小。
    """
    w = max(1, int(w))
    h = max(1, int(h))
    total = w * h
    if total < _MIN_TOTAL_PX:
        scale = (_MIN_TOTAL_PX / total) ** 0.5
    elif total > _MAX_TOTAL_PX:
        scale = (_MAX_TOTAL_PX / total) ** 0.5
    else:
        scale = 1.0
    w2 = max(2, int(w * scale) // 2 * 2)
    h2 = max(2, int(h * scale) // 2 * 2)
    return "{}x{}".format(w2, h2)


class AIImageGenTool(BaseTool):
    name = "ai_image_generate"
    agent_callable = True
    description = "从文字提示生成图片，产物落地本地并返回路径（支持参考图/尺寸/种子）"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图片描述"},
            "width": {"type": "integer", "default": 1024, "description": "期望宽度（按比例收敛到模型合法区间）"},
            "height": {"type": "integer", "default": 576, "description": "期望高度"},
            "size": {"type": "string", "description": "直传官方 size（如 2K / 2048x2048），优先于 width/height"},
            "image": {"type": "string", "description": "参考图 URL 或 base64（可选，图生图）"},
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
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    def is_available(self) -> bool:
        """按当前 provider 检查凭据是否配置（配置优先、env 兜底）。

        未配置时 ToolRegistry.list_agent_callable() 会隐藏本工具，
        Agent 侧不会把不可用工具暴露给 LLM 的 function schemas。
        """
        if self._provider == "volcengine":
            return bool(self._api_key or os.environ.get("ARK_API_KEY", ""))
        if self._provider == "dalle":
            return bool(self._api_key or os.environ.get("OPENAI_API_KEY", ""))
        if self._provider == "flux":
            return bool(self._api_key or os.environ.get("FLUX_API_KEY", ""))
        return True  # local SD 有默认地址

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        w = kwargs.get("width", 1024)
        h = kwargs.get("height", 576)
        try:
            if self._provider == "volcengine":
                return await self._gen_volcengine(prompt, w, h, kwargs)
            elif self._provider == "dalle":
                return await self._gen_dalle(prompt, w, h, kwargs.get("style", "natural"))
            elif self._provider == "flux":
                return await self._gen_flux(prompt, w, h)
            elif self._provider == "local":
                return await self._gen_local(prompt, w, h)
            return {"success": False, "error": "未知 provider: " + str(self._provider)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- 火山方舟 Seedream ----------------------------------------

    async def _gen_volcengine(self, prompt: str, w: int, h: int, params: dict) -> dict:
        key = self._api_key or os.environ.get("ARK_API_KEY", "")
        if not key:
            return {"success": False, "error": "ARK_API_KEY 未配置（或经插件配置 api_key）"}
        base = (self._base_url or os.environ.get("ARK_BASE_URL", "") or DEFAULT_ARK_BASE).rstrip("/")
        if not base.startswith("https://"):
            return {"success": False, "error": "ark_base_url 必须为 https://"}
        model = self._model or os.environ.get("ARK_SEEDREAM_MODEL", "") or DEFAULT_SEEDREAM_MODEL

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "watermark": False,
            "response_format": "url",
        }
        size = str(params.get("size", "") or "").strip()
        body["size"] = size if size else _snap_size(w, h)
        if params.get("image"):
            body["image"] = str(params["image"])
        if params.get("seed") is not None:
            body["seed"] = int(params["seed"])

        headers = {"Authorization": "Bearer " + key}
        async with httpx.AsyncClient(base_url=base, timeout=180) as c:
            resp = await c.post("/images/generations", headers=headers, json=body)
            resp.raise_for_status()
            data = (resp.json().get("data") or [{}])[0]
            local_path, remote_url = await self._materialize(c, data)
        record_generated("ai_image_gen", prompt=prompt, type="image", path=local_path,
                         resolution=body["size"])
        return {
            "success": True,
            "url": local_path,
            "path": local_path,
            "remote_url": remote_url,
            "model": model,
            "provider": "volcengine",
        }

    @staticmethod
    async def _materialize(client: httpx.AsyncClient, data: dict) -> tuple[str, str]:
        """把响应中的 b64_json 或 url 落地为本地文件，返回 (本地路径, 远端URL)。

        Seedream 返回的 url 24 小时失效，必须及时下载；下载前做 SSRF 校验。
        """
        out_dir = Path("PluginData/assets")
        out_dir.mkdir(parents=True, exist_ok=True)
        b64 = data.get("b64_json")
        if b64:
            raw = base64.b64decode(b64)
        else:
            remote = str(data.get("url", ""))
            assert_public_url(remote)
            r = await client.get(remote)
            r.raise_for_status()
            raw = r.content
        ext = "png" if raw[:4] == bytes([0x89]) + b"PNG" else "jpg"
        out = out_dir / ("ai_img_" + uuid.uuid4().hex[:12] + "." + ext)
        out.write_bytes(raw)
        return str(out), str(data.get("url", ""))

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
                record_generated("ai_image_gen", prompt=prompt, type="image", path=out,
                                 resolution=str(w) + "x" + str(h))
                return {"success": True, "url": out, "provider": "local"}
            return {"success": False, "error": "本地 SD 未返回图片"}


class AIImageGenPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="ai_image_gen", name="AI Image Generation", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="Generate images from text via Volcengine Seedream (default) / DALL-E / Flux / local SD",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        cfg = self.config or {}
        provider = cfg.get("provider", "volcengine")
        tool = AIImageGenTool(
            provider=provider,
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("ark_base_url", ""),
            model=cfg.get("model", ""),
        )
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        MaterialRegistry.register(
            make_generated_source("ai_image_gen", "AI 生成图片", "image"),
            plugin_id=self.manifest.id)
        logger.info("[AIImageGen] Tool + MaterialSource 已注册 (provider=%s)", provider)

    def shutdown(self) -> None:
        pass


__all__ = ["AIImageGenPlugin"]
