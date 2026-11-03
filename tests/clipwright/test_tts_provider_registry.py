"""M5 测试：TTS provider 注册表化（B-2）——B3-T6 之后的语义。

2026-11-03（B3-T6 / 裁定 3）之前，engine 会用 `ensure_tts_providers_registered()`
补位三个内置 provider；之后**没有内置注册**，三个 id 全部由协议插件提供
（`tts_qwen` / `tts_cosyvoice` / `tts_minimax`），插件不在就是"没有 TTS 能力"。

所以本文件的断言从「内置登记 + 归属 __builtin__」改成「插件登记 + 归属插件」，
并显式断言**内置注册函数已不存在**（防止有人把它加回来，让裁定 3 悄悄失效）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.plugins.contribution import (
    ConflictPolicy,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.providers.tts import normalize_tts_name, resolve_tts_entry, tts_provider_names


@pytest.fixture(scope="module")
def loaded_plugins():
    """启动一次 app 让插件注册进贡献门面（本文件只读注册表，不卸载任何贡献）。"""
    from clipwright.main import app

    with TestClient(app, raise_server_exceptions=False):
        yield


class TestTtsRegistry:
    def test_three_ids_are_provided_by_plugins(self, loaded_plugins):
        assert set(tts_provider_names()) == {"qwen-tts", "cosyvoice", "minimax"}
        for name in tts_provider_names():
            owner = ContributionRegistry.owner_of(ContributionKind.TTS_PROVIDER, name)
            assert owner.startswith("tts_"), f"{name} 应由协议插件接管，实际 {owner!r}"

    def test_builtin_registration_function_is_gone(self, loaded_plugins):
        """裁定 3：engine 不再有「注册内置 TTS provider」的入口。"""
        import clipwright.providers.tts as _tts

        assert not hasattr(_tts, "ensure_tts_providers_registered"), (
            "内置 TTS provider 注册函数又回来了——B3-T6 已按裁定 3 删除它"
        )

    def test_alias_normalization_single_source(self, loaded_plugins):
        assert normalize_tts_name("qwen_tts") == "qwen-tts"
        assert normalize_tts_name("qwen-tts") == "qwen-tts"
        assert normalize_tts_name("cosyvoice") == "cosyvoice"
        assert normalize_tts_name("unknown") == "unknown"

    def test_resolve_entry_and_default_model_matches_settings(self, loaded_plugins):
        from clipwright.config import settings

        entry = resolve_tts_entry("qwen-tts")
        assert entry is not None and entry.name == "qwen-tts"
        # default_model 仍读同一 settings 键（行为与内置时代一致）
        assert entry.default_model() == settings.tts_qwen_model
        assert resolve_tts_entry("qwen_tts").name == "qwen-tts"  # 别名解析
        assert resolve_tts_entry("nope") is None

    def test_service_uses_registry(self, loaded_plugins):
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

    def test_unregistered_plugin_leaves_no_builtin_behind(self, loaded_plugins):
        """卸载协议插件 → 该 id 直接消失（不补位），恢复后归属回到插件。

        只动 qwen 一个 id，并在 finally 里按 loader 的方式注册回去，
        避免污染同进程里其它用例。
        """
        kind = ContributionKind.TTS_PROVIDER
        ContributionRegistry.unregister_keys("tts_qwen", [(kind, "qwen-tts")])
        try:
            assert "qwen-tts" not in tts_provider_names()
            assert resolve_tts_entry("qwen-tts") is None, "删除内置注册后不应再有补位"
        finally:
            from plugins.tts_qwen.main import QwenTtsProviderPlugin

            plugin = QwenTtsProviderPlugin()
            items = list(plugin.contributions() or [])
            ContributionRegistry.declared_scope(
                "tts_qwen", {f"{it.kind.value}:{it.id}" for it in items},
            )
            report = ContributionRegistry.register(
                "tts_qwen", items, ConflictPolicy.OVERRIDE,
            )
            assert not report.rejected, [r[1] for r in report.rejected]
        assert ContributionRegistry.owner_of(kind, "qwen-tts") == "tts_qwen"
