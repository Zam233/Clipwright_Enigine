"""语音提供商扩展插件 — 把 ElevenLabs / Azure 接成**真正的 TTS provider**。

2026-11-08 审计整改（B8-T8.7 / 审计 §3.15）：旧实现只贡献一个 `extended_tts` 工具，
两套协议完整但 **`VoiceService` 看不到它们**——音色下拉没有、`/api/voice/*` 也不认，
插件只能被 agent 当普通工具调用，走的是"另起一条路"。

现在贡献两条 `tts_provider`（与 qwen/cosyvoice/minimax 同一协议面）：
- `elevenlabs`：`/v1/text-to-speech/{voice_id}`；
- `azure`：`https://{region}.tts.speech.microsoft.com/cognitiveservices/v1`（SSML）。

凭证优先级：Provider 源绑定（`TtsCredentials`）> 插件配置 > 环境变量。
两者都**不支持本项目的音色克隆协议**（克隆接口会抛可读原因，而不是静默失败）。
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.providers.tts import TtsProviderEntry
from clipwright.schema.plugin import PluginManifest, PluginKind
from plugins.voice_ext.providers import AzureVoiceProvider, ElevenLabsVoiceProvider

#: 各 provider 的默认目标模型/音色（plugin 配置可覆盖）
_DEFAULT_MODELS = {
    "elevenlabs": "eleven_multilingual_v2",
    "azure": "zh-CN-XiaoxiaoNeural",
}

#: 环境变量（仅用于"是否配置了凭据"的提示；实际读取在 providers.py）
_ENV_KEYS = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "azure": "AZURE_SPEECH_KEY",
}


class VoiceExtPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="voice_ext", name="Voice Provider Extension", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="扩展 TTS provider（ElevenLabs / Azure），与内置音色源同协议面",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        cfg = dict(self.config or {})
        items: list[Any] = []
        for provider_id, cls in (
            ("elevenlabs", ElevenLabsVoiceProvider),
            ("azure", AzureVoiceProvider),
        ):
            default_model = str(
                cfg.get(f"{provider_id}_model") or _DEFAULT_MODELS[provider_id]
            )

            entry = TtsProviderEntry(
                name=provider_id,
                factory=lambda uploader, creds=None, _cls=cls: _cls(creds, cfg),
                default_model=lambda _m=default_model: _m,
                # 两者都直接吃文本 → 音频字节；不需要公网参考音频
                needs_uploader=False,
            )
            items.append(
                Contribution(
                    ContributionKind.TTS_PROVIDER, provider_id, entry,
                    meta={
                        "name": provider_id,
                        "default_model": default_model,
                        "env_key": _ENV_KEYS[provider_id],
                        "clone_supported": False,
                    },
                ),
            )
        logger.info(
            "[VoiceExt] 贡献 tts_provider: %s（凭证优先级：源绑定 > 插件配置 > 环境变量）",
            ", ".join(c.id for c in items),
        )
        return items

    def shutdown(self) -> None:
        pass


__all__ = ["VoiceExtPlugin"]
