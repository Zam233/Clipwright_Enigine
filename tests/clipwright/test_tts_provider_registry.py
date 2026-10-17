"""M5 测试：TTS provider 注册表化（B-2）。"""
from __future__ import annotations

import pytest

from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
from clipwright.providers.tts import (
    ensure_tts_providers_registered,
    normalize_tts_name,
    resolve_tts_entry,
    tts_provider_names,
)


class TestTtsRegistry:
    def test_builtin_registered(self):
        ensure_tts_providers_registered()
        assert set(tts_provider_names()) == {"qwen-tts", "cosyvoice", "minimax"}
        for name in tts_provider_names():
            assert ContributionRegistry.owner_of(
                ContributionKind.TTS_PROVIDER, name) == "__builtin__"

    def test_alias_normalization_single_source(self):
        assert normalize_tts_name("qwen_tts") == "qwen-tts"
        assert normalize_tts_name("qwen-tts") == "qwen-tts"
        assert normalize_tts_name("cosyvoice") == "cosyvoice"
        assert normalize_tts_name("unknown") == "unknown"

    def test_resolve_entry_and_default_model_matches_settings(self):
        from clipwright.config import settings

        entry = resolve_tts_entry("qwen-tts")
        assert entry is not None and entry.name == "qwen-tts"
        # default_model 与既有 _model_for 读同一 settings 键（行为不变）
        assert entry.default_model() == settings.tts_qwen_model
        assert resolve_tts_entry("qwen_tts").name == "qwen-tts"  # 别名解析
        assert resolve_tts_entry("nope") is None

    def test_service_uses_registry(self):
        """VoiceService._get_provider 走注册表：别名共享实例、未知名报可读错误。"""
        from clipwright.services.voice import VoiceService

        svc = VoiceService.__new__(VoiceService)
        svc._providers = {}
        svc._uploader = None
        p1 = svc._get_provider("qwen-tts")
        assert svc._get_provider("qwen_tts") is p1  # 别名 → 同一实例

        svc2 = VoiceService.__new__(VoiceService)
        svc2._providers = {}
        svc2._uploader = None
        assert svc2._get_provider("cosyvoice") is not None
        with pytest.raises(ValueError, match="Choose from"):
            svc2._get_provider("definitely-not-a-provider")
