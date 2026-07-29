"""T6: VoiceCloneTool + TextToSpeechTool tests — delegate to VoiceService via FakeProvider injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clipwright.schema.tool import ToolStatus
from clipwright.tool.voice import TextToSpeechTool, VoiceCloneTool


class _FakeVoiceService:
    """Minimal stub that satisfies VoiceCloneTool/TextToSpeechTool.execute()."""

    def __init__(self) -> None:
        self._clone_result = MagicMock(
            success=True,
            data={"id": "abc", "provider": "qwen-tts", "voice_id": "vid",
                  "voice_name": "test", "target_model": "qwen3-tts-vc-2026-01-22"},
            error="",
        )
        self._synth_result = MagicMock(
            success=True,
            data={"audio_path": "/tmp/audio.mp3", "duration_sec": 2.5,
                  "text": "hello", "voice_id": "vid", "provider": "qwen-tts"},
            error="",
        )
        self.clone_count = 0
        self.synth_count = 0

    async def clone(self, **kwargs: Any) -> MagicMock:
        self.clone_count += 1
        return self._clone_result

    async def synthesize(self, **kwargs: Any) -> MagicMock:
        self.synth_count += 1
        return self._synth_result


@pytest.fixture()
def fake_svc():
    svc = _FakeVoiceService()
    with patch("clipwright.services.voice.get_voice_service", return_value=svc):
        yield svc


class TestVoiceCloneTool:
    def test_name_and_description(self):
        t = VoiceCloneTool()
        assert t.name == "voice_clone"
        assert "克隆" in t.description
        assert t.dependencies == []

    @pytest.mark.asyncio
    async def test_execute_success(self, fake_svc: _FakeVoiceService):
        result = await VoiceCloneTool().execute(
            audio_path="/tmp/sample.wav", voice_name="my_voice"
        )
        assert result.status == ToolStatus.SUCCESS
        assert result.output["voice_id"] == "vid"
        assert result.output["voice_name"] == "test"
        assert fake_svc.clone_count == 1

    @pytest.mark.asyncio
    async def test_execute_failure(self, fake_svc: _FakeVoiceService):
        fake_svc._clone_result = MagicMock(
            success=False, data={}, error="Key not configured",
        )
        result = await VoiceCloneTool().execute(
            audio_path="/tmp/sample.wav", voice_name="x"
        )
        assert result.status == ToolStatus.ERROR
        assert "Key" in result.error


class TestTextToSpeechTool:
    def test_name_and_description(self):
        t = TextToSpeechTool()
        assert t.name == "text_to_speech"
        assert "TTS" in t.description
        assert t.dependencies == []

    @pytest.mark.asyncio
    async def test_execute_success(self, fake_svc: _FakeVoiceService):
        result = await TextToSpeechTool().execute(
            text="你好", voice_id="vid"
        )
        assert result.status == ToolStatus.SUCCESS
        assert result.output["audio_path"] == "/tmp/audio.mp3"
        assert result.output["duration_sec"] == 2.5
        assert result.output_path == "/tmp/audio.mp3"
        assert fake_svc.synth_count == 1

    @pytest.mark.asyncio
    async def test_execute_failure(self, fake_svc: _FakeVoiceService):
        fake_svc._synth_result = MagicMock(
            success=False, data={}, error="Voice not found",
        )
        result = await TextToSpeechTool().execute(
            text="test", voice_id="bad"
        )
        assert result.status == ToolStatus.ERROR
        assert "not found" in result.error.lower()
