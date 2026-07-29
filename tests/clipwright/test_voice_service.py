"""T5: VoiceService orchestration tests — clone / synthesize / dub_script / list / delete.

Uses FakeProvider injection + tmp_path for zero real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from clipwright.services.voice import (
    BaseVoiceProvider,
    VoiceResult,
    VoiceService,
    VoiceStorage,
    get_voice_service,
)


# ──────────────────────────────────────────────
# FakeProvider for testing
# ──────────────────────────────────────────────


class FakeProvider(BaseVoiceProvider):
    """Deterministic test double: clone returns fixed ID, synthesize returns fixed bytes."""

    def __init__(self) -> None:
        self.clone_count = 0
        self.synth_count = 0
        self.fail_clone = False
        self.fail_synth = False
        self.fail_synth_on_text: str = ""

    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        self.clone_count += 1
        if self.fail_clone:
            raise RuntimeError("Fake clone error")
        return "fake-voice-id", voice_name or "fake_voice"

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> bytes:
        self.synth_count += 1
        if self.fail_synth:
            raise RuntimeError("Fake synth error")
        if self.fail_synth_on_text and self.fail_synth_on_text in text:
            raise RuntimeError(f"Fake synth error on: {text}")
        return b"\xff\xfb\x90\x00" * len(text)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _make_service(tmp_path: Path, fake: FakeProvider | None = None) -> VoiceService:
    """Create a VoiceService with temp dirs and optional FakeProvider injection."""
    db = tmp_path / "voices.json"
    out = tmp_path / "audio"
    up = tmp_path / "uploads"
    svc = VoiceService(db_path=db, output_dir=out, upload_dir=up)
    if fake:
        svc._providers["qwen-tts"] = fake
        svc._providers["cosyvoice"] = fake
        svc._providers["minimax"] = fake
    return svc


# ──────────────────────────────────────────────
# Clone tests
# ──────────────────────────────────────────────


_FAKE_SETTINGS_SNAPSHOT = {
    "tts_dashscope_api_key": "sk-fake-test-key",
    "tts_default_provider": "qwen-tts",
    "tts_qwen_model": "qwen3-tts-vc-2026-01-22",
    "tts_cosyvoice_model": "",
    "tts_minimax_model": "",
}


def _mock_settings(**overrides):
    """Return a MagicMock with the settings attributes clone() reads."""
    m = MagicMock(**{**_FAKE_SETTINGS_SNAPSHOT, **overrides})
    return m


class TestVoiceServiceClone:
    @pytest.mark.asyncio
    async def test_clone_no_key(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        with patch("clipwright.services.voice.settings", _mock_settings(tts_dashscope_api_key="")):
            result = await svc.clone(audio_path="dummy.wav", voice_name="test")
        assert result.success is False
        assert "Key" in result.error

    @pytest.mark.asyncio
    async def test_clone_with_data_uri(self, tmp_path: Path):
        fake = FakeProvider()
        svc = _make_service(tmp_path, fake)
        with patch("clipwright.services.voice.settings", _mock_settings()):
            result = await svc.clone(
                data_uri="data:audio/wav;base64,AAAA",
                voice_name="my_voice",
            )
        assert result.success is True
        assert result.data["voice_id"] == "fake-voice-id"
        assert result.data["voice_name"] == "my_voice"
        assert result.data["provider"] == "qwen-tts"
        assert fake.clone_count == 1
        stored = svc.list_voices()
        assert len(stored) == 1
        assert stored[0]["voice_id"] == "fake-voice-id"

    @pytest.mark.asyncio
    async def test_clone_with_audio_url(self, tmp_path: Path):
        fake = FakeProvider()
        svc = _make_service(tmp_path, fake)
        with patch("clipwright.services.voice.settings", _mock_settings()):
            result = await svc.clone(
                audio_url="https://example.com/sample.wav",
                voice_name="url_voice",
            )
        assert result.success is True
        assert result.data["voice_name"] == "url_voice"

    @pytest.mark.asyncio
    async def test_clone_provider_error(self, tmp_path: Path):
        fake = FakeProvider()
        fake.fail_clone = True
        svc = _make_service(tmp_path, fake)
        with patch("clipwright.services.voice.settings", _mock_settings()):
            result = await svc.clone(
                data_uri="data:audio/wav;base64,AAAA",
                voice_name="x",
            )
        assert result.success is False
        assert "Fake clone error" in result.error

    @pytest.mark.asyncio
    async def test_clone_no_audio_source(self, tmp_path: Path):
        svc = _make_service(tmp_path, FakeProvider())
        with patch("clipwright.services.voice.settings", _mock_settings()):
            result = await svc.clone(voice_name="test")
        assert result.success is False
        assert "audio" in result.error.lower() or "请提供" in result.error


# ──────────────────────────────────────────────
# Synthesize tests
# ──────────────────────────────────────────────


class TestVoiceServiceSynthesize:
    def _seed_voice(self, svc: VoiceService) -> str:
        """Seed a voice record and return its db id."""
        rec = {
            "id": "seed123",
            "provider": "qwen-tts",
            "voice_id": "fake-voice-id",
            "voice_name": "seed_voice",
            "target_model": "qwen3-tts-vc-2026-01-22",
            "created_at": "2026-07-21T10:00:00+00:00",
        }
        svc._storage.add(rec)
        return rec["id"]

    @pytest.mark.asyncio
    async def test_synthesize_writes_file(self, tmp_path: Path):
        fake = FakeProvider()
        svc = _make_service(tmp_path, fake)
        db_id = self._seed_voice(svc)
        result = await svc.synthesize(voice_id=db_id, text="你好世界")
        assert result.success is True
        assert result.data["audio_url"].startswith("/voice_audio/")
        assert result.data["duration_sec"] >= 0
        assert result.data["text"] == "你好世界"
        assert result.data["voice_id"] == db_id
        # Verify file exists
        audio_path = Path(result.data["audio_path"])
        assert audio_path.exists()
        assert audio_path.stat().st_size > 0
        assert fake.synth_count == 1

    @pytest.mark.asyncio
    async def test_synthesize_unknown_voice(self, tmp_path: Path):
        svc = _make_service(tmp_path, FakeProvider())
        result = await svc.synthesize(voice_id="nonexistent", text="test")
        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_synthesize_custom_output_path(self, tmp_path: Path):
        fake = FakeProvider()
        svc = _make_service(tmp_path, fake)
        db_id = self._seed_voice(svc)
        # output_path 必须位于服务输出目录内（安全约束）
        custom = tmp_path / "audio" / "custom_out.mp3"
        result = await svc.synthesize(
            voice_id=db_id, text="test", output_path=str(custom)
        )
        assert result.success is True
        assert Path(result.data["audio_path"]) == custom
        assert custom.exists()

    @pytest.mark.asyncio
    async def test_synthesize_rejects_output_path_outside_dir(self, tmp_path: Path):
        fake = FakeProvider()
        svc = _make_service(tmp_path, fake)
        db_id = self._seed_voice(svc)
        evil = tmp_path / "elsewhere" / "evil.mp3"
        result = await svc.synthesize(
            voice_id=db_id, text="test", output_path=str(evil)
        )
        assert result.success is False
        assert not evil.exists()

    @pytest.mark.asyncio
    async def test_synthesize_provider_error(self, tmp_path: Path):
        fake = FakeProvider()
        fake.fail_synth = True
        svc = _make_service(tmp_path, fake)
        db_id = self._seed_voice(svc)
        result = await svc.synthesize(voice_id=db_id, text="test")
        assert result.success is False
        assert "Fake synth error" in result.error


# ──────────────────────────────────────────────
# dub_script tests
# ──────────────────────────────────────────────


class TestVoiceServiceDubScript:
    def _seed_voice(self, svc: VoiceService) -> str:
        rec = {
            "id": "dub123",
            "provider": "qwen-tts",
            "voice_id": "fake-voice-id",
            "voice_name": "dub_voice",
            "target_model": "qwen3-tts-vc-2026-01-22",
            "created_at": "2026-07-21T10:00:00+00:00",
        }
        svc._storage.add(rec)
        return rec["id"]

    @pytest.mark.asyncio
    async def test_dub_script_segments(self, tmp_path: Path):
        fake = FakeProvider()
        svc = _make_service(tmp_path, fake)
        db_id = self._seed_voice(svc)
        result = await svc.dub_script(
            voice_id=db_id, text="句一。句二！"
        )
        assert result.success is True
        segments = result.data["segments"]
        assert len(segments) == 2
        assert segments[0]["index"] == 0
        assert segments[1]["index"] == 1
        assert "seed" in segments[0]
        assert result.data["total"] == 2
        assert result.data["total_duration_sec"] >= 0
        assert fake.synth_count == 2

    @pytest.mark.asyncio
    async def test_dub_script_empty_text(self, tmp_path: Path):
        svc = _make_service(tmp_path, FakeProvider())
        db_id = self._seed_voice(svc)
        result = await svc.dub_script(voice_id=db_id, text="")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_dub_script_partial_failure(self, tmp_path: Path):
        fake = FakeProvider()
        fake.fail_synth_on_text = "句二"
        svc = _make_service(tmp_path, fake)
        db_id = self._seed_voice(svc)
        result = await svc.dub_script(
            voice_id=db_id, text="句一。句二！"
        )
        # Overall success (partial results are acceptable)
        assert result.success is True
        segments = result.data["segments"]
        assert len(segments) == 2
        # First segment should have audio
        assert "audio_path" in segments[0]
        # Second segment should have error
        assert "error" in segments[1]


# ──────────────────────────────────────────────
# CRUD tests
# ──────────────────────────────────────────────


class TestVoiceServiceCRUD:
    def test_list_voices(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        assert svc.list_voices() == []
        svc._storage.add({"id": "a", "voice_name": "A"})
        assert len(svc.list_voices()) == 1

    def test_get_voice(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc._storage.add({"id": "b", "voice_name": "B"})
        v = svc.get_voice("b")
        assert v is not None
        assert v["voice_name"] == "B"
        assert svc.get_voice("nonexistent") is None

    def test_delete_voice(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc._storage.add({"id": "c", "voice_name": "C"})
        assert svc.delete_voice("c") is True
        assert svc.get_voice("c") is None
        assert svc.delete_voice("c") is False  # already deleted


# ──────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────


class TestGetVoiceService:
    def test_singleton_returns_same_instance(self):
        import clipwright.services.voice as mod
        mod._voice_service = None  # reset singleton
        s1 = get_voice_service()
        s2 = get_voice_service()
        assert s1 is s2
        mod._voice_service = None  # cleanup
