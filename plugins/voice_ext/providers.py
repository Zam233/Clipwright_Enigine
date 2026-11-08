"""扩展 TTS Provider 实现（ElevenLabs / Azure）——**能力方：插件**。

2026-11-08 审计整改（B8-T8.7 / 审计 §3.15）：旧实现把两套协议塞在一个 `tool`
（`extended_tts`）里，于是 `VoiceService` 的音色下拉与 `/api/voice/*` **看不到**
它们——能力在插件里，但用户和编排都摸不到，等于白写。

现在改成两条 `tts_provider` 贡献（`TtsProviderEntry` + `BaseVoiceProvider` 子类），
与 qwen/cosyvoice/minimax 同一协议面：`VoiceService` 自动解析、`voice.dub` 可选用。
凭证优先读 Provider 源绑定（`TtsCredentials`），其次插件配置/环境变量。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from clipwright.services.voice import BaseVoiceProvider, TtsCredentials


class ElevenLabsVoiceProvider(BaseVoiceProvider):
    """ElevenLabs（`/v1/text-to-speech/{voice_id}`）。"""

    name = "elevenlabs"

    def __init__(self, creds: TtsCredentials | None = None, plugin_config: dict | None = None) -> None:
        super().__init__(creds)
        self._cfg = dict(plugin_config or {})

    def _key(self) -> str:
        return (self._api_key(
            str(self._cfg.get("elevenlabs_api_key") or os.environ.get("ELEVENLABS_API_KEY", ""))
        ) or "").strip()

    async def clone(
        self, *, audio_ref: str, voice_name: str, target_model: str = "", audition_text: str = "",
    ) -> tuple[str, str]:
        raise RuntimeError(
            "ElevenLabs 不支持本项目的音色克隆协议：请在 ElevenLabs 控制台创建 voice_id，"
            "然后在配音设置里直接填该 voice_id"
        )

    async def synthesize(
        self, *, voice_api_id: str, model: str, text: str, **kwargs: Any,
    ) -> tuple[bytes, list[dict]]:
        key = self._key()
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY 未配置（可在插件配置页设置）")
        voice = voice_api_id or "21m00Tcm4TlvDq8ikWAM"
        model_id = model or "eleven_multilingual_v2"
        async with self._http(timeout=120.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers=self._headers({"xi-api-key": key}),
                json={"text": text, "model_id": model_id},
            )
            resp.raise_for_status()
            return resp.content, []


class AzureVoiceProvider(BaseVoiceProvider):
    """Azure Speech（SSML → `cognitiveservices/v1`）。"""

    name = "azure"

    def __init__(self, creds: TtsCredentials | None = None, plugin_config: dict | None = None) -> None:
        super().__init__(creds)
        self._cfg = dict(plugin_config or {})

    def _key(self) -> str:
        return (self._api_key(
            str(self._cfg.get("azure_speech_key") or os.environ.get("AZURE_SPEECH_KEY", ""))
        ) or "").strip()

    def _region(self) -> str:
        return str(
            self._cfg.get("azure_speech_region")
            or os.environ.get("AZURE_SPEECH_REGION", "")
            or "eastus"
        ).strip()

    async def clone(
        self, *, audio_ref: str, voice_name: str, target_model: str = "", audition_text: str = "",
    ) -> tuple[str, str]:
        raise RuntimeError(
            "Azure 不支持本项目的音色克隆协议：请使用 Azure 预置的 voice 名"
            "（如 zh-CN-XiaoxiaoNeural）"
        )

    async def synthesize(
        self, *, voice_api_id: str, model: str, text: str, **kwargs: Any,
    ) -> tuple[bytes, list[dict]]:
        key = self._key()
        if not key:
            raise RuntimeError("AZURE_SPEECH_KEY 未配置（可在插件配置页设置）")
        voice = voice_api_id or model or "zh-CN-XiaoxiaoNeural"
        ssml = (
            '<speak version="1.0" xml:lang="zh-CN">'
            f'<voice name="{voice}">{text}</voice></speak>'
        )
        async with self._http(timeout=120.0) as client:
            resp = await client.post(
                f"https://{self._region()}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers=self._headers({
                    "Ocp-Apim-Subscription-Key": key,
                    "Content-Type": "application/ssml+xml",
                }),
                content=ssml.encode("utf-8"),
            )
            resp.raise_for_status()
            return resp.content, []


def synthesize_to_file(provider: BaseVoiceProvider, **kwargs: Any) -> str:
    """同步包装（兼容旧 `extended_tts` 工具的落盘语义，测试/脚本用）。"""
    data, _segments = asyncio.run(provider.synthesize(**kwargs))
    return data.decode("latin-1", errors="ignore")


__all__ = ["ElevenLabsVoiceProvider", "AzureVoiceProvider"]
