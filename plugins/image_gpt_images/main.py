"""OpenAI GPT-Images 生图 Provider 插件（image_gpt_images）。

专有知识（image-provider-plugin PRD M5）：
- `size` 只接受固定档位：`1024x1024` / `1536x1024` / `1024x1536` / `auto`；
- `quality`: low / medium / high / auto；`background`: transparent / opaque / auto；
- 响应以 `b64_json` 为主（gpt-image-1 不再支持 `response_format=url` 参数，
  url 仅在个别网关兼容时出现，同样走 SSRF 校验后下载）。

Engine 只负责校验与编排；这里的参数语义是厂商知识，不进 Engine。
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.providers.base import (
    GeneratedImage,
    ImageGenerationRequest,
    ImageProviderCapabilities,
)

#: gpt-image-1 的固定 size 档位
VALID_SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")
VALID_QUALITY = ("low", "medium", "high", "auto")


class GPTImagesProvider:
    """OpenAI gpt-image-1 专用 provider（协议实现，不写盘/不记账）。"""

    name = "openai_gpt_image"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "gpt-image-1",
        quality: str = "auto",
        timeout_sec: float = 180.0,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip() or "gpt-image-1"
        self.quality = str(quality or "auto").strip().lower()
        self.timeout_sec = float(timeout_sec or 180.0)

    def _configure(self, request: ImageGenerationRequest) -> None:
        cfg = dict((request.extra or {}).get("source_config") or {})
        if cfg.get("base_url"):
            self.base_url = str(cfg["base_url"]).strip().rstrip("/")
        if cfg.get("api_key"):
            self.api_key = str(cfg["api_key"])
        if cfg.get("model"):
            self.model = str(cfg["model"]).strip() or "gpt-image-1"
        if cfg.get("quality"):
            self.quality = str(cfg["quality"]).strip().lower()
        if cfg.get("timeout_sec"):
            self.timeout_sec = float(cfg["timeout_sec"])

    def default_model(self) -> str:
        return self.model or "gpt-image-1"

    def capabilities(self, model: str = "") -> ImageProviderCapabilities:
        return ImageProviderCapabilities(
            sizes=VALID_SIZES,
            max_n=10,
            reference_image=False,
            output_formats=("png", "jpeg", "webp"),
            max_prompt_chars=32000,
            concurrency=2,
            cost_per_image_usd=None,
            size_dialect="openai_size",
        )

    def _apply_source_config(self, config: dict | None) -> None:
        cfg = dict(config or {})
        if cfg.get("base_url"):
            self.base_url = str(cfg["base_url"]).strip().rstrip("/")
        if cfg.get("api_key"):
            self.api_key = str(cfg["api_key"])
        if cfg.get("model"):
            self.model = str(cfg["model"]).strip() or self.model
        if cfg.get("timeout_sec"):
            self.timeout_sec = float(cfg["timeout_sec"])

    def model_listing_spec(self) -> dict | None:
        """OpenAI 兼容网关的模型清单端点（Bearer 鉴权）。"""
        return {"path": "/models", "auth": "bearer"}

    async def probe(self, config: dict | None = None) -> bool:
        """鉴权探测：gpt-image-1 无轻量握手，这里打 `/models` 只看 401/403。"""
        self._apply_source_config(config)
        if not self.base_url or not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            return resp.status_code not in (401, 403)
        except Exception:  # noqa: BLE001
            return False

    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        self._configure(request)
        if not self.base_url:
            raise ValueError("未配置 base_url（请在「供应商 → AI 生图」里填写）")
        if not self.api_key:
            raise ValueError("未配置 api_key（请在「供应商 → AI 生图」里填写）")
        prompt = str(request.prompt or "")
        if not prompt:
            raise ValueError("prompt 不能为空")

        size = str(request.size or "auto")
        if size not in VALID_SIZES:
            raise ValueError(
                f"gpt-image-1 的 size 只支持 {' / '.join(VALID_SIZES)}，收到 {size!r}"
            )
        quality = self.quality if self.quality in VALID_QUALITY else "auto"

        body: dict[str, Any] = {
            "model": request.model or self.default_model(),
            "prompt": prompt,
            "size": size,
            "n": max(1, min(int(request.n or 1), 10)),
            "quality": quality,
        }

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            resp = await client.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
        if resp.status_code != 200:
            detail = ""
            try:
                data = resp.json()
                detail = str((data.get("error") or {}).get("message")
                             or data.get("message") or "")[:300]
            except Exception:  # noqa: BLE001
                detail = (resp.text or "")[:300]
            raise RuntimeError(f"图像生成失败（HTTP {resp.status_code}）: {detail}")

        data = resp.json().get("data") or []
        out: list[GeneratedImage] = []
        for item in data:
            b64 = item.get("b64_json")
            url = str(item.get("url") or "")
            if b64:
                out.append(GeneratedImage(data=base64.b64decode(b64), mime="image/png"))
            elif url:
                from clipwright.security import assert_public_url

                assert_public_url(url)
                async with httpx.AsyncClient(timeout=self.timeout_sec, follow_redirects=True) as client:
                    dl = await client.get(url)
                if dl.status_code != 200:
                    raise RuntimeError(f"下载生成图失败（HTTP {dl.status_code}）: {url}")
                ctype = (dl.headers.get("content-type") or "image/png").split(";")[0]
                if not ctype.startswith("image/") or "svg" in ctype:
                    raise RuntimeError(f"拒绝非位图响应: {ctype}")
                out.append(GeneratedImage(data=dl.content, url=url, mime=ctype))
        if not out:
            raise RuntimeError("厂商响应中没有图像数据（data 为空）")
        return out


class GPTImagesPlugin(BasePlugin):
    """OpenAI gpt-images provider 插件。"""

    def __init__(self) -> None:
        super().__init__()
        self._provider: GPTImagesProvider | None = None

    def setup(self) -> None:
        self._provider = GPTImagesProvider()

    def contributions(self) -> list[Any]:
        assert self._provider is not None
        return [
            Contribution(
                ContributionKind.IMAGE_PROVIDER, "openai_gpt_image",
                self._provider,
                meta={"name": "OpenAI GPT-Images"},
            ),
        ]

    def shutdown(self) -> None:
        self._provider = None
