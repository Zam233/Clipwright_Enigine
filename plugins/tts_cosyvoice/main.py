"""CosyVoice TTS 协议插件（Batch 3 / B3-T2）。

只提供**协议层** provider：鉴权、请求构造、音频解析、`needs_uploader` 行为契约。
不拥有配音工作流（切句/时间轴/字幕对齐在 `plugins/tts_feature/`）。

实现位置：就在本目录（`provider.py`，B7-T7.1 迁移完成）；engine 只留抽象基类与宿主基座。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class CosyVoiceProviderPlugin(BasePlugin):
    """cosyvoice 协议层。"""

    def contributions(self) -> list[Any]:
        from clipwright.providers.tts import TtsProviderEntry
        from plugins.tts_cosyvoice.provider import CosyVoiceProvider

        entry = TtsProviderEntry(
            name="cosyvoice",
            factory=lambda uploader, creds=None: CosyVoiceProvider(uploader, creds),
            default_model=lambda: _default_model(),
            needs_uploader=True,
        )
        return [
            Contribution(
                ContributionKind.TTS_PROVIDER, "cosyvoice", entry,
                meta={"name": "cosyvoice", "needs_uploader": True},
            ),
        ]


def _default_model() -> str:
    from clipwright.config import settings

    return settings.tts_cosyvoice_model
