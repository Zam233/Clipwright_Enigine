"""AI 生图 Provider 插件（image_providers）—— 通用 OpenAI 兼容实现。

职责边界（image-provider-plugin PRD §2）：
- 本插件只做「协议 ↔ 厂商」翻译：`POST {base_url}/images/generations`、
  Bearer 鉴权、`b64_json` / `url` 两种响应解析、错误转可读中文；
- **不做**：落盘（Engine 第二期 `image_generate` 工具的职责）、账本、
  时间线修改、SSRF 校验由插件在下载 url 前先做一次，Engine 侧仍会复检。

`gpt-image-1` 这类有专有参数的模型请用 `image_gpt_images` 插件（M5）。
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


class OpenAICompatibleImageProvider:
    """OpenAI 兼容图像 provider（协议实现，不含任何落盘/账本逻辑）。"""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout_sec: float = 120.0,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip()
        self.timeout_sec = float(timeout_sec or 120.0)

    def _configure(self, request: ImageGenerationRequest) -> None:
        """把源配置（Engine 在构造时注入的 kwargs）合并进实例。

        `extra` 里的 `source_config` 由调用方（第二期 service / probe）传入，
        插件不主动读配置文件——配置归宿主。
        """
        cfg = dict((request.extra or {}).get("source_config") or {})
        if cfg.get("base_url"):
            self.base_url = str(cfg["base_url"]).strip().rstrip("/")
        if cfg.get("api_key"):
            self.api_key = str(cfg["api_key"])
        if cfg.get("model"):
            self.model = str(cfg["model"]).strip()
        if cfg.get("timeout_sec"):
            self.timeout_sec = float(cfg["timeout_sec"])

    def default_model(self) -> str:
        return self.model or "gpt-image-1"

    def capabilities(self, model: str = "") -> ImageProviderCapabilities:
        # 能力声明写在 manifest（表单/协商的唯一来源），这里返回与 manifest
        # 一致的结构化对象；size_dialect=wxh。
        return ImageProviderCapabilities(
            sizes="any",
            max_n=4,
            reference_image=False,
            output_formats=("png", "jpeg", "webp"),
            max_prompt_chars=4000,
            concurrency=2,
            cost_per_image_usd=None,
            size_dialect="wxh",
        )

    async def probe(self) -> bool:
        """鉴权探测：没有统一的图像模型列表协议，这里只验证
        「能带上 Bearer 头发一个最小请求且不是 401/403」。
        """
        if not self.base_url or not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            return resp.status_code not in (401, 403)
        except Exception:  # noqa: BLE001 — 探测失败一律 False
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

        body: dict[str, Any] = {
            "model": request.model or self.default_model(),
            "prompt": prompt,
            "n": max(1, int(request.n or 1)),
            "response_format": "b64_json",
        }
        if request.size:
            body["size"] = request.size

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            resp = await client.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
        if resp.status_code != 200:
            # 厂商错误转可读中文，不改写语义
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
                out.append(GeneratedImage(
                    data=base64.b64decode(b64),
                    mime="image/png",
                ))
            elif url:
                from clipwright.security import assert_public_url

                assert_public_url(url)  # SSRF：下载前先过白名单
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


class ImageProvidersPlugin(BasePlugin):
    """AI 生图 provider 插件：贡献一个通用 OpenAI 兼容实现。"""

    def __init__(self) -> None:
        super().__init__()
        self._provider: OpenAICompatibleImageProvider | None = None

    def setup(self) -> None:
        # 源配置由宿主在调用时经 extra.source_config 注入；这里只建实例骨架。
        self._provider = OpenAICompatibleImageProvider()

    def contributions(self) -> list[Any]:
        assert self._provider is not None
        return [
            Contribution(
                ContributionKind.IMAGE_PROVIDER, "openai_compatible",
                self._provider,
                meta={
                    "name": "OpenAI 兼容生图网关",
                    "compatible": True,
                },
            ),
        ]

    def shutdown(self) -> None:
        self._provider = None
