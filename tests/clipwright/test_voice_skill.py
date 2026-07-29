"""T7: DubScriptSkill tests — delegates to VoiceService via mock injection."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clipwright.schema.skill import SkillStatus
from clipwright.skill.dub import DubScriptSkill


class _FakeVoiceService:
    def __init__(self) -> None:
        self._dub_result = MagicMock(
            success=True,
            data={
                "segments": [
                    {"index": 0, "text": "句一", "audio_path": "/a/1.mp3", "audio_url": "/voice_audio/1.mp3", "duration_sec": 1.2, "seed": 42},
                    {"index": 1, "text": "句二", "audio_path": "/a/2.mp3", "audio_url": "/voice_audio/2.mp3", "duration_sec": 1.5, "seed": 43},
                ],
                "total": 2,
                "total_duration_sec": 2.7,
            },
            error="",
        )
        self.dub_count = 0

    async def dub_script(self, **kwargs: Any) -> MagicMock:
        self.dub_count += 1
        return self._dub_result


@pytest.fixture()
def fake_svc():
    svc = _FakeVoiceService()
    with patch("clipwright.services.voice.get_voice_service", return_value=svc):
        yield svc


class TestDubScriptSkill:
    def test_name_and_description(self):
        s = DubScriptSkill()
        assert s.name == "dub_script"
        assert "配音" in s.description
        assert s.required_tools == []

    @pytest.mark.asyncio
    async def test_execute_success(self, fake_svc: _FakeVoiceService):
        result = await DubScriptSkill().execute(
            voice_id="vid", text="句一。句二！"
        )
        assert result.status == SkillStatus.SUCCESS
        assert result.output["total_segments"] == 2
        assert result.output["total_duration_sec"] == 2.7
        assert len(result.output["segments"]) == 2
        assert fake_svc.dub_count == 1

    @pytest.mark.asyncio
    async def test_execute_failure(self, fake_svc: _FakeVoiceService):
        fake_svc._dub_result = MagicMock(
            success=False, data={}, error="Voice not found",
        )
        result = await DubScriptSkill().execute(
            voice_id="bad", text="test"
        )
        assert result.status == SkillStatus.ERROR
        assert "not found" in result.error.lower()

    def test_is_available_no_tools(self):
        assert DubScriptSkill().is_available() is True
