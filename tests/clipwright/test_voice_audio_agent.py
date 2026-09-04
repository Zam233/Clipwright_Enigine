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



# ── Caption generation (Todo 2 / C1b) ──

def _caption_track(tl: Timeline) -> Track | None:
    """返回时间线上的字幕轨（kind == TEXT），不存在则返回 None。"""
    for t in tl.tracks:
        if t.kind == ClipKind.TEXT:
            return t
    return None


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_auto_dub_generates_caption_clips(mock_registry: object) -> None:
    """(a) 自动配音后，字幕轨出现 2 条 CAPTION clip，起止/时长与旁白分段对齐。"""
    mock_registry.execute = AsyncMock(return_value=_dub_result())
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    text_track = _caption_track(out.timeline)
    assert text_track is not None
    assert text_track.name == "字幕轨"
    caps = [c for c in text_track.clips if c.kind == ClipKind.CAPTION]
    assert len(caps) == 2
    assert caps[0].start_sec == 0.0
    assert caps[0].duration_sec == 2.0
    assert caps[0].text == "甲。"
    assert caps[1].start_sec == 2.0
    assert caps[1].duration_sec == 3.0
    assert caps[1].text == "乙。"
    assert caps[0].font_size == 36
    assert caps[0].font_color == "#ffffff"
    assert caps[0].metadata["category"] == "caption"
    assert caps[0].metadata["renderer"] == "drawtext"
    assert caps[0].metadata["position"] == "bottom"
    # 字幕 clip 按 start_sec 排序
    assert [c.start_sec for c in text_track.clips] == sorted(c.start_sec for c in text_track.clips)
    # 字幕轨 index 不与既有轨道冲突
    assert text_track.index not in {t.index for t in out.timeline.tracks if t.id != text_track.id}


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_captions_disabled_when_subtitle_enabled_false(mock_registry: object) -> None:
    """(b) subtitle_enabled=False 时，旁白照常生成，但不再生成字幕。"""
    mock_registry.execute = AsyncMock(return_value=_dub_result())
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()
    inp = AudioInput(
        context=ctx, timeline=tl,
        audio_config={"voice_id": "v1", "auto_dub": True, "subtitle_enabled": False},
    )

    out = await agent.execute(inp, ctx)

    narr_tracks = [t for t in out.timeline.tracks if t.id == "a_narration"]
    assert len(narr_tracks) == 1
    assert len(narr_tracks[0].clips) == 2  # 旁白不受影响
    assert _caption_track(out.timeline) is None  # 无字幕轨 / 字幕


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_no_captions_when_dub_fails(mock_registry: object) -> None:
    """(c) dub 失败 → 无字幕、无异常。"""
    mock_registry.execute = AsyncMock(return_value=SkillExecResult(
        status=SkillStatus.ERROR,
        skill_name="dub_script",
        error="no provider",
    ))
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    assert _caption_track(out.timeline) is None


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_no_captions_when_no_segments(mock_registry: object) -> None:
    """(c) dub 成功但无分段 → 无字幕、无异常。"""
    mock_registry.execute = AsyncMock(return_value=_dub_result(segments=[]))
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    assert _caption_track(out.timeline) is None


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_caption_text_truncated_to_100(mock_registry: object) -> None:
    """(d) 超过 100 字字幕文本被截断到 ≤100。"""
    long_text = "字" * 150
    mock_registry.execute = AsyncMock(return_value=_dub_result(
        segments=[{"audio_path": "/a.mp3", "duration_sec": 2.0, "text": long_text, "seed": 1}],
    ))
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="x")
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    text_track = _caption_track(out.timeline)
    assert text_track is not None
    caps = [c for c in text_track.clips if c.kind == ClipKind.CAPTION]
    assert len(caps) == 1
    assert len(caps[0].text) <= 100
    assert caps[0].text == long_text[:100]


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_uploaded_dub_mode_no_captions(mock_registry: object) -> None:
    """上传配音模式（audio_path 提供）即使有脚本，也不生成字幕。"""
    mock_registry.execute = AsyncMock(return_value=_dub_result())
    agent = AudioAgent()
    ctx = _ctx(
        video_mode="voiceover", script_text="甲。乙。",
        audio_path="/uploaded.mp3", audio_duration_sec=10.0,
    )
    tl = _make_timeline()
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    assert _caption_track(out.timeline) is None


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_existing_caption_not_duplicated(mock_registry: object) -> None:
    """stale_state: 已有字幕覆盖同一分段时不重复生成；仅生成缺失的 2s 段。"""
    mock_registry.execute = AsyncMock(return_value=_dub_result())
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()
    tl.tracks.append(Track(
        id="text_t",
        name="字幕轨",
        kind=ClipKind.TEXT,
        index=0,
        clips=[
            Clip(
                id="existing_cap",
                kind=ClipKind.CAPTION,
                asset_id="",
                track_id="text_t",
                start_sec=0.0,
                duration_sec=2.0,
                text="甲。",
                metadata={"category": "caption", "renderer": "drawtext", "position": "bottom"},
            ),
        ],
    ))
    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})

    out = await agent.execute(inp, ctx)

    text_track = _caption_track(out.timeline)
    assert text_track is not None
    caps = [c for c in text_track.clips if c.kind == ClipKind.CAPTION]
    assert len(caps) == 2  # 0s 段被现有字幕覆盖 → 不重复；仅新增 2s 段
    assert {round(c.start_sec, 3) for c in caps} == {0.0, 2.0}


@pytest.mark.asyncio
@patch("clipwright.skill.registry.SkillRegistry")
async def test_rerun_does_not_duplicate_captions(mock_registry: object) -> None:
    """stale_state: 对已处理时间线重复执行，不产生重复字幕。"""
    mock_registry.execute = AsyncMock(return_value=_dub_result())
    agent = AudioAgent()
    ctx = _ctx(video_mode="voiceover", script_text="甲。乙。")
    tl = _make_timeline()

    inp = AudioInput(context=ctx, timeline=tl, audio_config={"voice_id": "v1", "auto_dub": True})
    out1 = await agent.execute(inp, ctx)
    inp2 = AudioInput(context=ctx, timeline=out1.timeline, audio_config={"voice_id": "v1", "auto_dub": True})
    out2 = await agent.execute(inp2, ctx)

    text_track = _caption_track(out2.timeline)
    assert text_track is not None
    caps = [c for c in text_track.clips if c.kind == ClipKind.CAPTION]
    assert len(caps) == 2  # 与首次运行一致，无重复
