"""MiniMax Provider 实现（插件拥有实现；engine 只留抽象与基座）。

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


class MiniMaxProvider(BaseVoiceProvider):
    """MiniMax：clone 需公网 URL + audition text，synth 走 HTTP（hex/base64/url 响应）。"""

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
        url = audio_ref
        if _is_local_url(url) and self._uploader:
            url = await self._uploader.maybe_upload(url)

        voice_id_str = voice_name or f"mini_{uuid.uuid4().hex[:8]}"
        payload = {
            "model": target_model or "MiniMax/speech-2.8-turbo",
            "input": {
                "action": "voice_clone",
                "voice_id": voice_id_str,
                "audio_url": url,
                "text": audition_text or "你好，欢迎使用音色克隆。",
            },
        }
        api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        logger.info("MiniMax clone -> POST %s", api_url)
        headers = {
            "Authorization": f"Bearer {self._api_key(settings.tts_dashscope_api_key)}",
            "Content-Type": "application/json",
        }
        headers = self._headers(headers)
        async with self._http() as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax clone failed [{resp.status_code}]: {resp.text[:500]}")
        return voice_id_str, voice_id_str

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> tuple[bytes, list[dict]]:
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        vs = {"voice_id": voice_api_id, "speed": 1, "vol": 1, "pitch": 0}
        emotion = kwargs.get("emotion")
        if emotion:
            vs["emotion"] = emotion
        inp: dict = {
            "text": text,
            "voice_setting": vs,
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        payload = {"model": model or "MiniMax/speech-2.8-turbo", "input": inp}
        headers = {
            "Authorization": f"Bearer {self._api_key(settings.tts_dashscope_api_key)}",
            "Content-Type": "application/json",
        }
        headers = self._headers(headers)
        logger.info("MiniMax synthesize -> POST %s", url)
        async with self._http() as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax synthesis failed [{resp.status_code}]: {resp.text[:500]}")
        body = resp.json()
        audio_data = body.get("output", {}).get("data", {})
        audio_bytes = b""
        if audio_data.get("audio"):
            audio_bytes = bytes.fromhex(audio_data["audio"])
        else:
            audio_info = body.get("output", {}).get("audio", {})
            if audio_info.get("data"):
                audio_bytes = base64.b64decode(audio_info["data"])
            elif audio_info.get("url"):
                async with self._http() as client:
                    r = await client.get(audio_info["url"])
                if r.status_code == 200:
                    audio_bytes = r.content
            else:
                ct = resp.headers.get("content-type", "")
                if "audio" in ct or "octet" in ct:
                    audio_bytes = resp.content
        if not audio_bytes:
            raise RuntimeError(f"MiniMax 响应中没有音频: {str(body)[:300]}")
        return audio_bytes, []


# ──────────────────────────────────────────────
# 编排层：VoiceService
# ──────────────────────────────────────────────
