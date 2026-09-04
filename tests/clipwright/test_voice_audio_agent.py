"""Tests for T9 — AudioAgent auto-dub narration gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.audio_agent import AudioAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AudioInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.schema.skill import SkillExecResult, SkillStatus


def _ctx(**extra: object) -> AgentContext:
    return AgentContext(
        pipeline_id="p_test",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
        extra_params={**extra},
    )


def _make_timeline(has_narration: bool = False) -> Timeline:
    tl = Timeline()
    if has_narration:
        tl.tracks.append(Track(
            id="a_narration",
            name="旁白 TTS",
            kind=ClipKind.AUDIO,
            index=0,
            clips=[
                Clip(
                    id="existing_narr",
                    kind=ClipKind.AUDIO,
                    asset_id="/existing.mp3",
                    track_id="a_narration",
                    start_sec=0.0,
                    duration_sec=2.0,
                    metadata={"narration": True},
                ),
            ],
        ))
    return tl


def _dub_result(segments: list | None = None) -> SkillExecResult:
    if segments is None:
        segments = [
            {"audio_path": "/a.mp3", "duration_sec": 2.0, "text": "甲。", "seed": 1},
            {"audio_path": "/b.mp3", "duration_sec": 3.0, "text": "乙。", "seed": 2},
        ]
    return SkillExecResult(
        status=SkillStatus.SUCCESS,
        skill_name="dub_script",
        output={"segments": segments, "total": len(segments), "total_duration_sec": sum(s["duration_sec"] for s in segments)},
    )


# ── Happy path ──

@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_auto_dub_creates_narration_track(mock_registry_cls: object) -> None:
    mock_registry_cls.execute = AsyncMock(return_value=_dub_result())
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 1
    narr = narr_tracks[0]
    assert len(narr.clips) == 2
    assert narr.clips[0].start_sec == 0.0
    assert narr.clips[0].duration_sec == 2.0
    assert narr.clips[1].start_sec == 2.0
    assert narr.clips[1].duration_sec == 3.0
    assert narr.clips[0].metadata["narration"] is True
    assert narr.clips[0].metadata["voice_id"] == "v1"
    assert narr.clips[0].metadata["text"] == "甲。"
    assert out.timeline.duration_sec >= 5.0
    assert any("自动配音: 2 段旁白" in n for n in out.audio_notes)


# ── Skip cases ──

@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_skip_no_voice(mock_registry: object) -> None:
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={})

    out = await agent.execute(inp, ctx)

    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 0
    mock_registry.execute.assert_not_called()


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_skip_empty_script(mock_registry: object) -> None:
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="  ")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1"})

    out = await agent.execute(inp, ctx)

    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 0
    mock_registry.execute.assert_not_called()


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_skip_not_voiceover(mock_registry: object) -> None:
    agent = AudioAgent()
    ctx = _ctx(video_mode="other", script_text="甲。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1"})

    out = await agent.execute(inp, ctx)

    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 0
    mock_registry.execute.assert_not_called()


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_skip_has_narration(mock_registry: object) -> None:
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline(has_narration=True)
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    narr_track = [t for t in out.timeline.tracks if t.id == "a_narration"][0]
    assert len(narr_track.clips) == 1  # existing clip not touched
    mock_registry.execute.assert_not_called()


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_skip_auto_dub_false(mock_registry: object) -> None:
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": False})

    out = await agent.execute(inp, ctx)

    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 0
    mock_registry.execute.assert_not_called()


# ── Exception handling ──

@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_dub_exception_does_not_fail(mock_registry: object) -> None:
    mock_registry.execute = AsyncMock(side_effect=RuntimeError("boom"))
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    # Decision stays PASS, no narration track, error in notes
    assert out.decision == AgentDecision.PASS
    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 0
    assert any("自动配音失败" in n for n in out.audio_notes)


# ── Service-level dub failure ──

@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_dub_service_error(mock_registry: object) -> None:
    mock_registry.execute = AsyncMock(return_value=SkillExecResult(
        status=SkillStatus.ERROR,
        skill_name="dub_script",
        error="no provider",
    ))
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 0
    assert any("自动配音失败" in n for n in out.audio_notes)


# ── BGM logic untouched ──

@pytest.mark.asyncio
async def test_bgm_logic_untouched() -> None:
    """Verify BGM logic still works with no auto-dub triggered."""
    agent = AudioAgent()
    ctx = _ctx(video_mode="other", script_text="")
    tl = Timeline()
    tl.tracks.append(Track(
        id="a_bgm",
        name="BGM",
        kind=ClipKind.AUDIO,
        index=0,
        clips=[
            Clip(
                id="bgm1",
                kind=ClipKind.AUDIO,
                asset_id="/bgm.mp3",
                track_id="a_bgm",
                start_sec=0.0,
                duration_sec=10.0,
            ),
        ],
    ))
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"bgm_slots": {"intro": ["rock"]}})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    bgm_clip = tl.tracks[0].clips[0]
    assert bgm_clip.metadata.get("bpm") == 120
    assert bgm_clip.metadata.get("bgm_slot") == "intro"
    # B6: 淡入淡出改用 audio_fade 真实曲线——不再把首个 clip 音量硬压 0.3
    assert bgm_clip.volume == 1.0
    assert bgm_clip.audio_fade_in_sec == 1.0
    assert bgm_clip.audio_fade_out_sec == 2.0
