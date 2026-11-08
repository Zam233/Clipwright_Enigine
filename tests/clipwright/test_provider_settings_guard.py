"""B7-T7.5：provider 设置项 → 注册表校验（不抛异常、只报未知）。"""
from __future__ import annotations

import pytest

from clipwright.services.provider_settings_guard import (
    normalize_provider_setting,
    warn_unknown_provider_settings,
)


class _Cfg:
    def __init__(self, llm: str = "", embed: str = "", tts: str = "") -> None:
        self.llm_provider = llm
        self.rag_embed_provider = embed
        self.tts_default_provider = tts


@pytest.fixture(autouse=True)
def _plugins_registered():
    """把三类 provider 插件显式装上（等价于"插件已安装"）。"""
    from clipwright.plugins.contribution import ContributionRegistry
    from plugins.embedding_providers.main import EmbeddingProvidersPlugin
    from plugins.llm_anthropic.main import AnthropicProviderPlugin
    from plugins.llm_openai_compatible.main import OpenACompatibleProviderPlugin
    from plugins.tts_qwen.main import QwenTtsProviderPlugin

    owners: list[str] = []
    for owner, plugin in (
        ("psg_llm_a", AnthropicProviderPlugin()),
        ("psg_llm_o", OpenACompatibleProviderPlugin()),
        ("psg_embed", EmbeddingProvidersPlugin()),
        ("psg_tts", QwenTtsProviderPlugin()),
    ):
        items = plugin.contributions()
        ContributionRegistry.declared_scope(owner, [c.key for c in items])
        report = ContributionRegistry.register(owner, items)
        assert report.ok, report.rejected
        owners.append(owner)
    try:
        yield
    finally:
        for owner in owners:
            ContributionRegistry.unregister_plugin(owner)


class TestNormalize:
    def test_embedding_legacy_alias(self) -> None:
        assert normalize_provider_setting("sentence_transformer") == "local"
        assert normalize_provider_setting("sentence_transformers") == "local"

    def test_tts_legacy_alias(self) -> None:
        assert normalize_provider_setting("qwen_tts") == "qwen-tts"

    def test_unknown_passthrough(self) -> None:
        assert normalize_provider_setting("my-own-tts") == "my-own-tts"


class TestWarnUnknown:
    def test_known_ids_produce_no_warning(self) -> None:
        cfg = _Cfg(llm="anthropic", embed="sentence_transformer", tts="qwen_tts")
        assert warn_unknown_provider_settings(cfg) == []

    def test_unknown_ids_are_reported_not_raised(self) -> None:
        cfg = _Cfg(llm="nope-llm", embed="nope-embed", tts="nope-tts")
        unknown = warn_unknown_provider_settings(cfg)
        assert len(unknown) == 3
        assert all("=" in item for item in unknown)

    def test_empty_values_skipped(self) -> None:
        assert warn_unknown_provider_settings(_Cfg()) == []
