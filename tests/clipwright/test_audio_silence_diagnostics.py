"""Tests for T4 — silent-audio diagnostics (AudioAgent warnings + render _mix_audio log)."""

from __future__ import annotations

import logging

import pytest

from clipwright.agents.audio_agent import AudioAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AudioInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.render import RenderService


def _ctx(**extra: object) -> AgentContext:
    return AgentContext(
        pipeline_id="p_test_silence",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
        extra_params={**extra},
    )


def _placeholder_timeline() -> Timeline:
    """音频轨只有占位 clip（asset_id 为空）的时间线。"""
    tl = Timeline()
    tl.tracks.append(
        Track(
            id="a_main",
            name="音频轨",
            kind=ClipKind.AUDIO,
            index=0,
            clips=[
                Clip(
                    id="ph_1",
                    kind=ClipKind.AUDIO,
                    asset_id="",
                    track_id="a_main",
                    start_sec=0.0,
                    duration_sec=10.0,
                ),
                Clip(
                    id="ph_2",
                    kind=ClipKind.AUDIO,
                    asset_id="",
                    track_id="a_main",
                    start_sec=10.0,
                    duration_sec=10.0,
                ),
            ],
        )
    )
    return tl


@pytest.mark.asyncio
async def test_audio_agent_warns_when_no_audio(monkeypatch) -> None:
    """音频轨全是占位 clip、无旁白且无 demo 配音 → notes 必须含无声音警告。"""
    # 屏蔽 demo 配音回退，使警告路径可达
    monkeypatch.setattr(AudioAgent, "_resolve_demo_voice", staticmethod(lambda: ""))
    agent = AudioAgent()
    ctx = _ctx()
    inp = AudioInput(context=ctx, timeline=_placeholder_timeline(), audio_config={})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    joined = "\n".join(out.audio_notes)
    assert "无配音" in joined or "无声音" in joined


@pytest.mark.asyncio
async def test_audio_agent_falls_back_to_demo_voice(monkeypatch) -> None:
    """无真实音频且 demo voice.mp3 存在 → 用 demo 配音铺满时间线（不再无声）。"""
    agent = AudioAgent()
    ctx = _ctx()
    tl = _placeholder_timeline()
    # 模拟 demo 配音存在（避免依赖本机文件）
    demo = str(monkeypatch._tmpdir if hasattr(monkeypatch, "_tmpdir") else ".")
    fake = "/demo/voice.mp3"
    monkeypatch.setattr(AudioAgent, "_resolve_demo_voice", staticmethod(lambda: fake))
    monkeypatch.setattr(AudioAgent, "_probe_demo_duration", staticmethod(lambda p: 20.0))
    inp = AudioInput(context=ctx, timeline=tl, audio_config={})

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    # 占位 clip 被清空，插入 demo 配音 clip
    audio_track = next(t for t in tl.tracks if t.kind == ClipKind.AUDIO)
    assert len(audio_track.clips) == 1
    assert audio_track.clips[0].asset_id == fake
    assert audio_track.clips[0].start_sec == 0.0
    assert audio_track.clips[0].duration_sec == 20.0
    assert "demo" in "\n".join(out.audio_notes).lower()


@pytest.mark.asyncio
async def test_audio_agent_notes_gate_failure() -> None:
    """有文案但 voice_id 为空 → notes 说明配音门控失败原因。"""
    agent = AudioAgent()
    ctx = _ctx(script_text="这是一段需要配音的文案。", video_mode="voiceover")
    inp = AudioInput(
        context=ctx,
        timeline=_placeholder_timeline(),
        audio_config={"voice_id": "", "auto_dub": True},
    )

    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    joined = "\n".join(out.audio_notes)
    assert "配音未触发" in joined
    assert "voice_id" in joined


@pytest.mark.asyncio
async def test_render_mix_audio_logs_silent(
    tmp_path, caplog: pytest.LogCaptureFixture,
) -> None:
    """_mix_audio 找不到配音/BGM → logger.warning + 直接拷贝输入。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 64)
    out = tmp_path / "out.mp4"

    rs = RenderService(work_dir=tmp_path / "work")

    with caplog.at_level(logging.WARNING, logger="clipwright"):
        await rs._mix_audio(str(src), [], str(out))

    assert out.exists()
    assert any(
        "无声音" in rec.message and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )
