"""provider 设置项与注册表的启动期校验（B7-T7.5）。

为什么需要它：`config.py` 里的 `llm_provider` / `rag_embed_provider` / `tts_default_provider`
原本是 `Literal[...]` 白名单——那只认 engine 认识的三个 id，第三方插件声明的 provider
（例如 `qwen-tts` 之外的音色源、或自建 LLM 网关）根本填不进去。改成 `str` 之后，
"这个 id 到底存不存在"就必须由**运行期注册表**回答，而不是由类型注解回答。

语义（不抛异常）：未知 id 只记一条 info 日志，调用路径按"能力缺失"处理（口径 A）。
之所以不把值清空/不回退默认值：用户填的 id 可能是"插件暂时被禁用"，静默改写会让人
以为配置丢了。
"""
from __future__ import annotations

from typing import Any

from clipwright.config import logger, settings as _settings

#: 遗留别名（settings 历史值与门面 id 的差异；只做归一，不改变写入值）
_LEGACY_ALIASES = {
    "sentence_transformer": "local",
    "sentence_transformers": "local",
}


def _registry_has(kind_value: str, provider_id: str) -> bool:
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    return ContributionRegistry.resolve(ContributionKind(kind_value), provider_id) is not None


def normalize_provider_setting(value: str) -> str:
    """settings 里的历史值 → 门面 id（当前只有嵌入族有别名）。"""
    from clipwright.providers.tts import normalize_tts_name

    text = str(value or "").strip()
    return _LEGACY_ALIASES.get(text, normalize_tts_name(text))


def warn_unknown_provider_settings(settings: Any = None) -> list[str]:
    """校验三项 provider 设置；返回未知 id 列表（同时逐条 info 日志）。"""
    cfg = settings or _settings
    checks = (
        ("llm_provider", "llm_provider", "LLM"),
        ("rag_embed_provider", "embedding_provider", "嵌入"),
        ("tts_default_provider", "tts_provider", "TTS"),
    )
    unknown: list[str] = []
    for attr, kind_value, label in checks:
        raw = str(getattr(cfg, attr, "") or "").strip()
        if not raw:
            continue
        canonical = normalize_provider_setting(raw)
        try:
            found = _registry_has(kind_value, canonical)
        except Exception:  # noqa: BLE001 — 门面不可用不阻断启动
            found = True
        if not found:
            unknown.append(f"{attr}={raw}")
            logger.info(
                "%s provider「%s」不在注册表里（插件未安装/被禁用）；"
                "该项将在调用时按能力缺失处理，配置值保持原样",
                label, raw,
            )
    return unknown
