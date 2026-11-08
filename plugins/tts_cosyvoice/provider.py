"""CosyVoice Provider 实现（插件拥有实现；engine 只留抽象与基座）。

2026-11-08 能力迁移（审计 B7-T7.1）：类从 `clipwright/services/voice.py` 搬到这里；
engine 侧只留 `BaseVoiceProvider` 抽象、凭据/存储/上传等**宿主基座**，以及一个
**兼容别名**（既有导入与打桩路径继续有效，禁止在新代码里依赖它）。
"""
from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings
from clipwright.services.voice import (
    BaseVoiceProvider,
    PublicUploadManager,
    TtsCredentials,
    _is_local_url,
    _sanitize_name,
)


class CosyVoiceProvider(BaseVoiceProvider):
    """CosyVoice：clone 需公网 URL（PublicUpload），synth 走 dashscope SDK WebSocket。"""

    def __init__(
        self,
        uploader: Optional[PublicUploadManager] = None,
        creds: Optional[TtsCredentials] = None,
    ) -> None:
        super().__init__(creds)
        self._uploader = uploader

    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        # CosyVoice needs a public URL; auto-upload local URLs
        url = audio_ref
        if _is_local_url(url) and self._uploader:
            url = await self._uploader.maybe_upload(url)

        payload = {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": target_model or "cosyvoice-v3.5-plus",
                "prefix": _sanitize_name(voice_name, f"myvoice{uuid.uuid4().hex[:6]}", allow_extra=""),
                "url": url,
                "enable_preprocess": True,
                "max_prompt_audio_length": 30.0,
            },
        }
        api_url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        logger.info("CosyVoice clone -> POST %s", api_url)
        headers = {
            "Authorization": f"Bearer {self._api_key(settings.tts_dashscope_api_key)}",
            "Content-Type": "application/json",
        }
        headers = self._headers(headers)
        async with self._http() as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code != 200:
            detail = resp.text[:500]
            if "detect audio failed" in detail or "Audio.DecoderError" in detail:
                raise RuntimeError(
                    "CosyVoice 未能从音频中检测到有效人声。请确保音频：\n"
                    "1. 纯人声，无背景音乐/噪音\n"
                    "2. 至少 5 秒连续说话\n"
                    "3. 单人，正常语速\n"
                    "4. 时长 10-60 秒，文件 ≤ 10MB"
                )
            raise RuntimeError(f"CosyVoice clone failed [{resp.status_code}]: {detail}")

        body = resp.json()
        voice_id = body.get("voice_id") or body.get("output", {}).get("voice_id", "")
        if not voice_id:
            raise RuntimeError(f"CosyVoice returned no voice_id: {body}")
        return voice_id, payload["input"]["prefix"]

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> tuple[bytes, list[dict]]:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.base_websocket_api_url = (
            f"wss://{settings.tts_workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        )
        # SDK 仅支持全局 api_key：用锁串行化「改 key → 合成 → 还原」，避免并发请求相互覆盖凭据
        with _DASHSCOPE_KEY_LOCK:
            old_key = dashscope.api_key
            dashscope.api_key = self._api_key(settings.tts_dashscope_api_key)
            try:
                synth_kwargs: dict = dict(model=model, voice=voice_api_id)
                for key in ("instructions", "volume", "speech_rate", "pitch_rate", "seed"):
                    val = kwargs.get(key)
                    if val is not None:
                        synth_kwargs[key] = val
                synthesizer = SpeechSynthesizer(**synth_kwargs)
                audio = synthesizer.call(text)
                if audio is None:
                    raise RuntimeError("CosyVoice synthesis returned None")
                audio_bytes = audio if isinstance(audio, bytes) else audio.encode()
                return audio_bytes, []
            finally:
                dashscope.api_key = old_key
