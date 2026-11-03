"""Qwen-TTS 协议插件（Batch 3 补：把 `qwen-tts` 从内置注册搬进插件）。

**为什么补这个**：B3-T6 删除内置 TTS 注册后，没有插件接管的 id 会从下拉里消失。
`qwen-tts` 是历史最久的音色来源之一，先补插件再删内置，删除就不会造成功能回退
（见 docs/provider-pluginization-b3t6-removal-checklist.md §4）。

实现位置：与 `tts_cosyvoice` 同策略——协议类暂留 `services/voice.py`，插件接管注册与声明。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class QwenTtsProviderPlugin(BasePlugin):
    """qwen-tts 协议层。"""

    def contributions(self) -> list[Any]:
        from clipwright.providers.tts import TtsProviderEntry
        from clipwright.services import voice as _voice

        entry = TtsProviderEntry(
            name="qwen-tts",
            factory=lambda uploader, creds=None: _voice.QwenTTSProvider(creds),
            default_model=_default_model,
            needs_uploader=False,
        )
        return [
            Contribution(
                ContributionKind.TTS_PROVIDER, "qwen-tts", entry,
                meta={"name": "qwen-tts"},
            ),
        ]


def _default_model() -> str:
    from clipwright.config import settings

    return settings.tts_qwen_model
