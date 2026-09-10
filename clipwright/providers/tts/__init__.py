"""内置 TTS Provider 注册（方案 B-2）。

三个内置实现留在 `services/voice.py`（实现体不动）；本模块把它们以
「工厂 + 默认模型」形式登记进门面，`VoiceService._get_provider` 改为
门面解析——provider 名从此只有一个来源（contribution.id），别名兼容层
（P1-25 的 `qwen_tts`）集中在解析入口处理。

`default_model` 与 `voice.py::_model_for` 读取同一 settings 键，
保证行为不变（快照测试锁定）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

#: TTS 名称别名（历史 id → 规范 contribution.id），集中在此处维护
_TTS_ALIASES = {
    "qwen_tts": "qwen-tts",
}


@dataclass(frozen=True)
class TtsProviderEntry:
    """TTS provider 的门面载荷：工厂 + 默认模型 + 是否需要上传器。"""
    name: str
    factory: Callable[[Any], Any]
    default_model: Callable[[], str]
    needs_uploader: bool = False


def normalize_tts_name(name: str) -> str:
    return _TTS_ALIASES.get(str(name or ""), str(name or ""))


def ensure_tts_providers_registered() -> None:
    """登记三个内置 TTS provider（幂等）。"""
    from clipwright.config import settings
    from clipwright.plugins.contribution import (
        BUILTIN_OWNER,
        Contribution,
        ContributionKind,
        ContributionRegistry,
    )

    if ContributionRegistry.resolve(ContributionKind.TTS_PROVIDER, "qwen-tts") is not None:
        return

    def _make(name: str) -> TtsProviderEntry:
        from clipwright.services import voice as _voice

        if name == "qwen-tts":
            return TtsProviderEntry(
                name=name, factory=lambda uploader: _voice.QwenTTSProvider(),
                default_model=lambda: settings.tts_qwen_model,
            )
        if name == "cosyvoice":
            return TtsProviderEntry(
                name=name, factory=lambda uploader: _voice.CosyVoiceProvider(uploader),
                default_model=lambda: settings.tts_cosyvoice_model,
                needs_uploader=True,
            )
        return TtsProviderEntry(
            name=name, factory=lambda uploader: _voice.MiniMaxProvider(uploader),
            default_model=lambda: settings.tts_minimax_model,
            needs_uploader=True,
        )

    ContributionRegistry.register(BUILTIN_OWNER, [
        Contribution(ContributionKind.TTS_PROVIDER, n, _make(n))
        for n in ("qwen-tts", "cosyvoice", "minimax")
    ], dispatch=False)


def resolve_tts_entry(name: str) -> Optional[TtsProviderEntry]:
    """解析 TTS provider 条目（含别名归一 + 懒注册）；未知 → None。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_tts_providers_registered()
    impl = ContributionRegistry.resolve(
        ContributionKind.TTS_PROVIDER, normalize_tts_name(name),
    )
    return impl.object if impl is not None else None


def tts_provider_names() -> list[str]:
    """已注册的 TTS provider 名列表（错误信息与前端选项共用）。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_tts_providers_registered()
    return [c.id for _, c in ContributionRegistry.list(ContributionKind.TTS_PROVIDER)]
