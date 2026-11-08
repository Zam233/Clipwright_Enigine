"""MiniMax TTS 协议插件（Batch 3 补：把 `minimax` 从内置注册搬进插件）。

实现位置：就在本目录（`provider.py`，B7-T7.1 迁移完成）。
`needs_uploader=True`——该 provider 需要公网 URL 才能工作（音色参考音频上传），
上传器本身是 engine 基座（`PublicUploadManager`），不属于插件。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class MiniMaxProviderPlugin(BasePlugin):
    """minimax 协议层。"""

    def contributions(self) -> list[Any]:
        from clipwright.providers.tts import TtsProviderEntry
        from plugins.tts_minimax.provider import MiniMaxProvider

        entry = TtsProviderEntry(
            name="minimax",
            factory=lambda uploader, creds=None: MiniMaxProvider(uploader, creds),
            default_model=_default_model,
            needs_uploader=True,
        )
        return [
            Contribution(
                ContributionKind.TTS_PROVIDER, "minimax", entry,
                meta={"name": "minimax", "needs_uploader": True},
            ),
        ]


def _default_model() -> str:
    from clipwright.config import settings

    return settings.tts_minimax_model
