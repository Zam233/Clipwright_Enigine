"""上传配音 → 字幕按配音实测时间重建（默认行为）。

两个真实故障的回归（2026-10-23 用户反馈）：
1. 上传配音后字幕不跟随配音——对齐逻辑被缩进在 `audio_config.sfx` 分支里，
   只有传了音效才跑；
2. 对齐不可用时静默保留字数估算，用户不知道"字幕没对上"。
"""

from __future__ import annotations

import pytest

from clipwright.agents.audio_agent import AudioAgent
from clipwright.schema.agent import AgentContext, AudioInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.stt import STTResult


def _ctx(audio_path: str, duration: float, **extra) -> AgentContext:
    return AgentContext(
        pipeline_id="p_align",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="配音对齐",
        extra_params={"audio_path": audio_path, "audio_duration_sec": duration, **extra},
    )


def _timeline_with_captions() -> Timeline:
    tl = Timeline(duration_sec=10.0)
    tl.tracks.append(Track(
        id="cap",
        name="字幕轨",
        kind=ClipKind.CAPTION,
        index=0,
        clips=[
            Clip(id="c1", kind=ClipKind.CAPTION, asset_id="", track_id="cap",
                 start_sec=0.0, duration_sec=4.0, text="甲。",
                 font_size=40, stroke_width=3.0, font_color="#ffd700",
                 metadata={"category": "caption", "position": "bottom", "renderer": "ass"}),
            Clip(id="c2", kind=ClipKind.CAPTION, asset_id="", track_id="cap",
                 start_sec=4.0, duration_sec=6.0, text="乙。",
                 font_size=40, stroke_width=3.0, font_color="#ffd700",
                 metadata={"category": "caption", "position": "bottom", "renderer": "ass"}),
        ],
    ))
    return tl


def _captions(tl: Timeline) -> list[Clip]:
    return [c for t in tl.tracks if t.kind == ClipKind.CAPTION for c in t.clips]


@pytest.mark.asyncio
async def test_uploaded_dub_aligns_captions_without_sfx(tmp_path, monkeypatch) -> None:
    """没有 SFX 也要对齐（旧实现被 sfx 分支挡住）。"""
    audio = tmp_path / "dub.wav"
    audio.write_bytes(b"RIFF0000WAVE")

    async def fake_align(self, audio_path, transcript_text, language=""):
        return STTResult(
            text=transcript_text, success=True, model="estimate",
            segments=[
                {"start": 0.0, "end": 1.0, "text": "甲。", "confidence": 0.9},
                {"start": 1.0, "end": 2.5, "text": "乙。", "confidence": 0.9},
            ],
        )

    monkeypatch.setattr("clipwright.services.stt.STTService.align", fake_align)
    tl = _timeline_with_captions()
    ctx = _ctx(str(audio), 2.5, video_mode="voiceover", script_text="甲。乙。")
    # audio_config 里没有 sfx / 没有 voice_id（不触发 TTS）——正是用户的上传场景
    out = await AudioAgent().execute(AudioInput(context=ctx, timeline=tl, audio_config={}), ctx)

    caps = _captions(out.timeline)
    assert [c.text for c in caps] == ["甲。", "乙。"]
    assert caps[0].start_sec == 0.0 and abs(caps[0].duration_sec - 1.0) < 1e-6
    assert abs(caps[1].start_sec - 1.0) < 1e-6
    assert abs(caps[1].duration_sec - 1.5) < 1e-6
    assert any("字幕对齐" in n for n in out.audio_notes)


@pytest.mark.asyncio
async def test_alignment_unavailable_is_not_silent(tmp_path, monkeypatch) -> None:
    """对齐不可用时要留痕（保留估算，但用户能看到原因）。"""
    audio = tmp_path / "dub.wav"
    audio.write_bytes(b"RIFF0000WAVE")

    async def fake_align(self, audio_path, transcript_text, language=""):
        return STTResult(text="", success=False, error="whisper 不可用", segments=[])

    monkeypatch.setattr("clipwright.services.stt.STTService.align", fake_align)
    tl = _timeline_with_captions()
    before = [(c.text, c.start_sec, c.duration_sec) for c in _captions(tl)]
    ctx = _ctx(str(audio), 10.0, video_mode="voiceover", script_text="甲。乙。")
    out = await AudioAgent().execute(AudioInput(context=ctx, timeline=tl, audio_config={}), ctx)

    assert [(c.text, c.start_sec, c.duration_sec) for c in _captions(out.timeline)] == before
    assert any("字幕对齐不可用" in n for n in out.audio_notes)


def test_realign_preserves_existing_caption_style() -> None:
    """重建时间轴时不洗掉既有样式（字体/字号/描边）。"""
    tl = _timeline_with_captions()
    AudioAgent._realign_captions_to_narration(tl, [
        {"text": "甲。", "start_sec": 0.2, "end_sec": 1.2, "audio_path": "x.wav"},
        {"text": "乙。", "start_sec": 1.2, "end_sec": 2.0, "audio_path": "x.wav"},
    ])
    caps = _captions(tl)
    assert [c.text for c in caps] == ["甲。", "乙。"]
    assert all(c.font_size == 40 and c.stroke_width == 3.0 for c in caps)
    assert all(c.font_color == "#ffd700" for c in caps)
    assert all((c.metadata or {}).get("source") == "narration_aligned" for c in caps)


def test_realign_ignores_segments_without_text() -> None:
    tl = _timeline_with_captions()
    AudioAgent._realign_captions_to_narration(tl, [
        {"text": "  ", "start_sec": 0.0, "end_sec": 1.0, "audio_path": "x.wav"},
    ])
    assert len(_captions(tl)) == 2  # 无有效段 → no-op


@pytest.mark.asyncio
async def test_segment_count_mismatch_keeps_caption_count(tmp_path, monkeypatch) -> None:
    """无标点长文 → STT 只切出 1 段时，仍按原字幕段数等比分时间（不能塌成一段）。"""
    audio = tmp_path / "dub.wav"
    audio.write_bytes(b"RIFF0000WAVE")

    async def fake_align(self, audio_path, transcript_text, language=""):
        return STTResult(
            text=transcript_text, success=True, model="estimate",
            segments=[{"start": 0.0, "end": 2.5, "text": "甲。乙。", "confidence": 0.5}],
        )

    monkeypatch.setattr("clipwright.services.stt.STTService.align", fake_align)
    tl = _timeline_with_captions()
    ctx = _ctx(str(audio), 2.5, video_mode="voiceover", script_text="甲。乙。")
    out = await AudioAgent().execute(AudioInput(context=ctx, timeline=tl, audio_config={}), ctx)

    caps = _captions(out.timeline)
    assert [c.text for c in caps] == ["甲。", "乙。"]      # 文案不变
    assert caps[0].start_sec == 0.0
    assert abs(caps[-1].start_sec + caps[-1].duration_sec - 2.5) < 1e-6
    assert any("等比分段" in n for n in out.audio_notes)
