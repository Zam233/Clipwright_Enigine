"""STT Provider 插件（Batch 4 / B4-T3）。

接管 `whisper` / `faster-whisper` 两个 `stt_provider`。插件禁用后内置补位，
字幕对齐/上传配音对齐不会因为插件消失而失去转写后端。

`SttBackendEntry` 的 `call` 由本插件提供，`service` 由调用方注入——与 engine
`ensure_stt_providers_registered()` 里的两份 `_call_*` 等价（同样的私有方法名），
避免出现「插件与内置行为不一致」。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


def _call_whisper(service: Any, audio_file: str, language: str,
                  model_size: str, word_timestamps: bool) -> Any:
    return service._transcribe_whisper(audio_file, language, model_size, word_timestamps)


def _call_faster(service: Any, audio_file: str, language: str,
                 model_size: str, word_timestamps: bool) -> Any:
    return service._transcribe_faster_whisper(
        audio_file, language, model_size, word_timestamps)


class SttProvidersPlugin(BasePlugin):
    """内置 STT 后端的插件化外壳。"""

    def contributions(self) -> list[Any]:
        from clipwright.providers.stt import (
            SttBackendEntry,
            _probe_faster_whisper,
            _probe_whisper,
        )

        return [
            Contribution(ContributionKind.STT_PROVIDER, "whisper", SttBackendEntry(
                name="whisper", is_available=_probe_whisper, call=_call_whisper,
            ), meta={"name": "whisper"}),
            Contribution(ContributionKind.STT_PROVIDER, "faster-whisper", SttBackendEntry(
                name="faster-whisper", is_available=_probe_faster_whisper, call=_call_faster,
            ), meta={"name": "faster-whisper"}),
        ]
