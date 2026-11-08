"""Qwen-TTS Provider 实现（**能力方**：插件拥有实现，engine 只留抽象与基座）。

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


class QwenTTSProvider(BaseVoiceProvider):
    """Qwen-TTS：clone 支持 base64 data URI（无需公网 URL），synth 走 HTTP。"""

    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        payload = {
            "model": "qwen-voice-enrollment",
            "input": {
                "action": "create",
                "target_model": target_model or "qwen3-tts-vc-2026-01-22",
                "preferred_name": _sanitize_name(voice_name, f"voice_{uuid.uuid4().hex[:6]}"),
                "audio": {"data": audio_ref},
            },
        }
        url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        logger.info("Qwen-TTS clone -> POST %s", url)
        headers = {
            "Authorization": f"Bearer {self._api_key(settings.tts_dashscope_api_key)}",
            "Content-Type": "application/json",
        }
        headers = self._headers(headers)
        async with self._http() as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            detail = resp.text[:500]
            raise RuntimeError(f"Qwen-TTS clone failed [{resp.status_code}]: {detail}")
        body = resp.json()
        voice_id = body.get("output", {}).get("voice", "")
        if not voice_id:
            raise RuntimeError(f"Qwen-TTS returned no voice_id: {body}")
        return voice_id, payload["input"]["preferred_name"]

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> tuple[bytes, list[dict]]:
        if "realtime" in model.lower():
            raise ValueError(
                f"音色绑定的是实时模型 '{model}'，不支持 HTTP 合成。"
                "请用 qwen3-tts-vc-2026-01-22 重新克隆音色。"
            )
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        inp: dict = {"text": text, "voice": voice_api_id}
        instructions = kwargs.get("instructions")
        if instructions:
            inp["instructions"] = instructions
        payload = {"model": model, "input": inp, "parameters": {"word_timestamp_enabled": True}}
        headers = {
            "Authorization": f"Bearer {self._api_key(settings.tts_dashscope_api_key)}",
            "Content-Type": "application/json",
        }
        headers = self._headers(headers)
        async with self._http() as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"Qwen-TTS 合成失败 [{resp.status_code}]: {resp.text[:500]}")
        body = resp.json()
        audio_info = body.get("output", {}).get("audio", {})
        audio_bytes = b""
        if audio_info.get("data"):
            audio_bytes = base64.b64decode(audio_info["data"])
        elif audio_info.get("url"):
            async with self._http() as client:
                r = await client.get(audio_info["url"])
            if r.status_code == 200:
                audio_bytes = r.content
        if not audio_bytes:
            raise RuntimeError(f"Qwen-TTS 响应中没有音频: {str(body)[:300]}")
        word_timestamps = body.get("output", {}).get("word_timestamps", [])
        return audio_bytes, word_timestamps

    async def build_audio_ref(self, path: Path) -> str:
        """Qwen 支持 base64 data URI：参考音频无需公网 URL。"""
        raw = path.read_bytes()
        mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}.get(
            path.suffix.lower(), "application/octet-stream"
        )
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
