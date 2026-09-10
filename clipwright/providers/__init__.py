"""providers 包 —— Provider 协议与内置实现（方案 B）。"""
from clipwright.providers.base import (
    LLMProvider,
    ProviderCapabilities,
    RendererBackend,
    STTProvider,
    TTSProvider,
)

__all__ = [
    "LLMProvider",
    "ProviderCapabilities",
    "RendererBackend",
    "STTProvider",
    "TTSProvider",
]
