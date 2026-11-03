"""TTS 功能插件的配音编排测试（B3-T4 迁出 / B3-T6 删内置后的等价覆盖）。

原来这部分覆盖分散在 `test_voice_skill.py`（内置 `DubScriptSkill`）与
`test_voice_service.py::TestVoiceServiceDubScript`（`VoiceService.dub_script`）里。
B3-T6（2026-11-03，裁定 3）把这两个实现一起删掉后，等价断言移到插件自己的实现上——
**不是降低覆盖**：编排逻辑现在只有一份，就是
`plugins.tts_feature.dub.DubScriptOrchestrator`；切句策略也随它搬到了
`plugins.tts_feature.split_text`。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from clipwright.schema.skill import SkillStatus
from clipwright.services.voice import VoiceResult
from plugins.tts_feature.dub import DubScriptOrchestrator


class _FakeService:
    """按段合成；`fail_on_text` 命中的片段返回失败（模拟单段失败）。"""

    def __init__(self, *, durations: dict[str, float] | None = None, fail_on_text: str = "") -> None:
        self.synth: list[str] = []
        self.seeds: list[int] = []
        self._durations = durations or {}
        self._fail_on_text = fail_on_text

    async def synthesize(self, *, voice_id: str, text: str, **kw: Any) -> VoiceResult:
        self.synth.append(text)
        self.seeds.append(int(kw.get("seed") or 0))
        if self._fail_on_text and self._fail_on_text in text:
            return VoiceResult(success=False, error="Fake synth error")
        dur = self._durations.get(text, 1.0)
        return VoiceResult(
            success=True,
            data={
                "audio_path": f"/a/{len(self.synth)}.mp3",
                "audio_url": f"/voice_audio/{len(self.synth)}.mp3",
                "duration_sec": dur,
                "voice_id": voice_id,
                "text": text,
            },
        )


def _patch_service(svc: _FakeService):
    return patch("clipwright.services.voice.get_voice_service", return_value=svc)


class TestDubScriptOrchestrator:
    def test_name_and_availability_contract(self):
        s = DubScriptOrchestrator()
        assert s.name == "dub_script"
        assert "配音" in s.description
        assert s.required_tools == []
        # `BaseSkill.is_available` 是**同步**契约（写成 async 会让 SkillInfo 校验炸）
        assert isinstance(s.is_available(), bool)

    @pytest.mark.asyncio
    async def test_segments_have_timeline_and_seed(self):
        svc = _FakeService(durations={"句一。": 1.2, "句二！": 1.5})
        with _patch_service(svc):
            result = await DubScriptOrchestrator().execute(voice_id="vid", text="句一。句二！")

        assert result.status == SkillStatus.SUCCESS
        segs = result.output["segments"]
        assert [s["index"] for s in segs] == [0, 1]
        assert segs[0]["start_sec"] == 0.0 and segs[0]["end_sec"] == 1.2
        assert segs[1]["start_sec"] == 1.2 and segs[1]["end_sec"] == 2.7
        assert "seed" in segs[0] and "char_timings" in segs[0]
        assert result.output["total"] == 2
        assert result.output["total_duration_sec"] == 2.7
        assert svc.synth == ["句一。", "句二！"]

    @pytest.mark.asyncio
    async def test_seed_is_deterministic_across_calls(self):
        """同 provider/voice/文案 → 同 seed（内置 hash() 带随机盐，这里必须用 sha256）。"""
        svc1, svc2 = _FakeService(), _FakeService()
        with _patch_service(svc1):
            await DubScriptOrchestrator().execute(voice_id="vid", text="句一。", provider="p1")
        with _patch_service(svc2):
            await DubScriptOrchestrator().execute(voice_id="vid", text="句一。", provider="p1")
        assert svc1.seeds == svc2.seeds and svc1.seeds[0] != 0

    @pytest.mark.asyncio
    async def test_empty_text_is_error(self):
        with _patch_service(_FakeService()):
            result = await DubScriptOrchestrator().execute(voice_id="vid", text="   ")
        assert result.status == SkillStatus.ERROR
        assert "文本为空" in result.error

    @pytest.mark.asyncio
    async def test_partial_failure_keeps_success(self):
        svc = _FakeService(fail_on_text="句二")
        with _patch_service(svc):
            result = await DubScriptOrchestrator().execute(voice_id="vid", text="句一。句二！")

        assert result.status == SkillStatus.SUCCESS
        segs = result.output["segments"]
        assert "audio_path" in segs[0]
        assert "error" in segs[1]

    @pytest.mark.asyncio
    async def test_all_failed_is_error(self):
        svc = _FakeService(fail_on_text="句")
        with _patch_service(svc):
            result = await DubScriptOrchestrator().execute(voice_id="vid", text="句一。句二！")
        assert result.status == SkillStatus.ERROR
        assert "Fake synth error" in result.error
