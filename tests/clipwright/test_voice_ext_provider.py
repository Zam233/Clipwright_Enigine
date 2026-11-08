"""B8-T8.7 / 审计 §3.15：voice_ext 必须以 `tts_provider` 接入（不是工具旁路）。"""

from __future__ import annotations

import pytest

from clipwright.plugins.contribution import ContributionRegistry
from clipwright.providers.tts import resolve_tts_entry, tts_provider_names
from plugins.voice_ext.main import VoiceExtPlugin


@pytest.fixture(autouse=True)
def _voice_ext_registered():
    owner = "voice_ext_test"
    items = VoiceExtPlugin().contributions()
    ContributionRegistry.declared_scope(owner, [c.key for c in items])
    report = ContributionRegistry.register(owner, items)
    assert report.ok, report.rejected
    try:
        yield
    finally:
        ContributionRegistry.unregister_plugin(owner)


class TestContributionShape:
    def test_contributes_tts_providers_only(self) -> None:
        items = VoiceExtPlugin().contributions()
        kinds = {c.kind.value for c in items}
        assert kinds == {"tts_provider"}, f"voice_ext 不应再贡献 {kinds - {'tts_provider'}}"
        assert {c.id for c in items} == {"elevenlabs", "azure"}

    def test_provider_ids_visible_to_voice_service(self) -> None:
        names = set(tts_provider_names())
        assert {"elevenlabs", "azure"} <= names

    def test_factory_returns_base_voice_provider(self) -> None:
        entry = resolve_tts_entry("elevenlabs")
        assert entry is not None
        provider = entry.factory(None, None)
        from clipwright.services.voice import BaseVoiceProvider

        assert isinstance(provider, BaseVoiceProvider)
        assert provider.name == "elevenlabs"
        assert entry.default_model() == "eleven_multilingual_v2"

    def test_disabled_plugin_removes_provider(self) -> None:
        ContributionRegistry.unregister_plugin("voice_ext_test")
        assert "elevenlabs" not in tts_provider_names()


class TestSynthesisErrors:
    @pytest.mark.asyncio
    async def test_missing_key_is_readable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        entry = resolve_tts_entry("elevenlabs")
        provider = entry.factory(None, None)
        with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
            await provider.synthesize(voice_api_id="v", model="m", text="hi")

    @pytest.mark.asyncio
    async def test_clone_unsupported_is_explicit(self) -> None:
        entry = resolve_tts_entry("azure")
        provider = entry.factory(None, None)
        with pytest.raises(RuntimeError, match="不支持"):
            await provider.clone(audio_ref="x", voice_name="n")
