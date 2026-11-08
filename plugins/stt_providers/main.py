"""STT Provider 插件（Batch 4 / B4-T3；2026-11-08 能力迁移后成为**唯一 owner**）。

接管 `whisper` / `faster-whisper` 两个 `stt_provider`：

- **探测**（`probe_whisper` / `probe_faster_whisper`）与**转写实现**
  （`transcribe_whisper` / `transcribe_faster_whisper`）都在本插件（`backends.py`）；
- engine `providers/stt` 只保留 `SttBackendEntry` + `resolve_stt_backends()`（只读注册表），
  不再懒注册内置补位；
- 插件被禁用 → 没有可用的 STT 后端（口径 A：能力缺失是一等状态）。

`call(service, audio_file, …)` 的 `service` 由调用方注入：`STTService` 的两个私有方法
现在只是 3 行转发到 `backends.py`，保留它是为了不打断既有调用面（无业务逻辑）。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.providers.stt import SttBackendEntry
from plugins.stt_providers.backends import (
    probe_faster_whisper,
    probe_whisper,
)


def _call_whisper(service: Any, audio_file: str, language: str,
                  model_size: str, word_timestamps: bool) -> Any:
    return service._transcribe_whisper(audio_file, language, model_size, word_timestamps)


def _call_faster(service: Any, audio_file: str, language: str,
                 model_size: str, word_timestamps: bool) -> Any:
    return service._transcribe_faster_whisper(
        audio_file, language, model_size, word_timestamps)


class SttProvidersPlugin(BasePlugin):
    """STT 后端（whisper / faster-whisper）的唯一 owner。"""

    def contributions(self) -> list[Any]:
        return [
            Contribution(ContributionKind.STT_PROVIDER, "whisper", SttBackendEntry(
                name="whisper", is_available=probe_whisper, call=_call_whisper,
            ), meta={"name": "whisper", "order": 10}),
            Contribution(ContributionKind.STT_PROVIDER, "faster-whisper", SttBackendEntry(
                name="faster-whisper", is_available=probe_faster_whisper, call=_call_faster,
            ), meta={"name": "faster-whisper", "order": 20}),
        ]
