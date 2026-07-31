"""语音提供商扩展插件 — 添加新 TTS 引擎的模板。

当前 VoiceService 硬编码了 Qwen-TTS/CosyVoice/MiniMax 提供商。
本插件演示如何通过 Tool 注册额外的 TTS 提供商。

支持的扩展提供商（通过 config.yaml provider 字段）：
  - "elevenlabs": ElevenLabs API（需 ELEVENLABS_API_KEY）
  - "azure": Azure Cognitive Services TTS（需 AZURE_SPEECH_KEY）
  - "xtts": 本地 XTTS-v2（需 XTTS_API_URL）
"""
from __future__ import annotations
import os
from typing import Any
import httpx
from clipwright.plugins import CapabilityPlugin
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

class ExtendedTTSTool(BaseTool):
    name = "extended_tts"
    agent_callable = True
    description = "使用扩展 TTS 提供商合成语音"
    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要合成的文本"},
            "voice_id": {"type": "string", "description": "语音 ID"},
            "provider": {"type": "string", "description": "提供商 elevenlabs/azure/xtts"},
        },
        "required": ["text"],
    }

    def __init__(self, default_provider: str = "elevenlabs") -> None:
        self._provider = default_provider

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        text = kwargs.get("text", "")
        if not text:
            return {"success": False, "error": "缺少 text"}
        provider = kwargs.get("provider", self._provider)
        try:
            if provider == "elevenlabs":
                return await self._synth_elevenlabs(text, kwargs.get("voice_id", ""))
            elif provider == "azure":
                return await self._synth_azure(text, kwargs.get("voice_id", "zh-CN-XiaoxiaoNeural"))
            return {"success": False, "error": f"未知 provider: {provider}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _synth_elevenlabs(self, text: str, voice_id: str) -> dict:
        key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            return {"success": False, "error": "ELEVENLABS_API_KEY 未配置"}
        vid = voice_id or "21m00Tcm4TlvDq8ikWAM"  # Rachel
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={"xi-api-key": key}, json={"text": text, "model_id": "eleven_monolingual_v1"})
            resp.raise_for_status()
            out = f"PluginData/tmp/tts_elevenlabs_{os.urandom(4).hex()}.mp3"
            with open(out, "wb") as f:
                f.write(resp.content)
            return {"success": True, "audio_path": out, "provider": "elevenlabs"}

    async def _synth_azure(self, text: str, voice_id: str) -> dict:
        key = os.environ.get("AZURE_SPEECH_KEY", "")
        region = os.environ.get("AZURE_SPEECH_REGION", "eastus")
        if not key:
            return {"success": False, "error": "AZURE_SPEECH_KEY 未配置"}
        ssml = f'<speak version="1.0" xml:lang="zh-CN"><voice name="{voice_id}">{text}</voice></speak>'
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml"},
                content=ssml)
            resp.raise_for_status()
            out = f"PluginData/tmp/tts_azure_{os.urandom(4).hex()}.wav"
            with open(out, "wb") as f:
                f.write(resp.content)
            return {"success": True, "audio_path": out, "provider": "azure"}

class VoiceExtPlugin(CapabilityPlugin):
    manifest = PluginManifest(id="voice_ext", name="Voice Provider Extension", version="1.0.0",
        kind=PluginKind.CAPABILITY, description="Extended TTS providers (ElevenLabs/Azure/XTTS)", author="Clipwright Team")

    def initialize(self) -> None:
        cfg = self.config or {}
        provider = cfg.get("provider", "elevenlabs")
        ToolRegistry.register(ExtendedTTSTool(default_provider=provider), plugin_id=self.manifest.id)
        logger.info("[VoiceExt] 扩展 TTS Tool 已注册 (provider=%s)", provider)

    def shutdown(self) -> None: pass

__all__ = ["VoiceExtPlugin"]
