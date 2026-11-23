"""图像 provider 的可选 batch 契约。"""

from __future__ import annotations

from clipwright.providers.base import (
    BatchImageProvider,
    ImageGenerationRequest,
    ImageProviderCapabilities,
)


class _BatchImpl:
    async def generate_batch(self, requests):
        return [[object()] for _ in requests]


def test_batch_protocol_is_structural() -> None:
    assert isinstance(_BatchImpl(), BatchImageProvider)
    assert ImageProviderCapabilities().supports_batch is False


def test_plugin_declares_batch() -> None:
    from plugins.image_generate.main import OpenAICompatibleImageProvider

    p = OpenAICompatibleImageProvider()
    caps = p.capabilities()
    assert caps.supports_batch is True
    assert callable(getattr(p, "generate_batch", None))
