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
    """TTS provider 的门面载荷：工厂 + 默认模型 + 是否需要上传器。

    B4：工厂签名为 ``factory(uploader, creds)``——creds 为 Provider 源
    「语音 TTS」角色绑定解析出的凭据（未绑定时为空凭据，行为回退 settings）。
    """
    name: str
    factory: Callable[..., Any]
    default_model: Callable[[], str]
    needs_uploader: bool = False


def normalize_tts_name(name: str) -> str:
    return _TTS_ALIASES.get(str(name or ""), str(name or ""))


def resolve_tts_entry(name: str) -> Optional[TtsProviderEntry]:
    """解析 TTS provider 条目（含别名归一）；未知 → None。

    注意：**不再懒注册内置**（B3-T6）。解析不到就是"没装协议插件"，
    调用方按能力缺失处理（`/api/voice/*` 给 503 + 可读原因）。
    """
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    impl = ContributionRegistry.resolve(
        ContributionKind.TTS_PROVIDER, normalize_tts_name(name),
    )
    return impl.object if impl is not None else None


def tts_provider_names() -> list[str]:
    """已注册的 TTS provider 名列表（错误信息与前端选项共用；全由插件贡献决定）。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    return [c.id for _, c in ContributionRegistry.list(ContributionKind.TTS_PROVIDER)]
