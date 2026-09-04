"""Tests for A1 — AudioAgent BGM from material library (with zero-change fallback).

覆盖：
- 素材库有 AUDIO 素材 → clip 元数据填充 bgm_library/bgm_style + note「BGM 来自素材库」
- MaterialRegistry.list() 为空 → 与基线行为完全一致（无 bgm_library、无素材库 note）
- 素材库只有 VIDEO 素材 → 回退原有 bgm_slots 规则，行为不变
- search 抛异常 → 不崩溃，静默回退
- 无音频 clip 且素材库有音频 → 占位建议 BGM 用素材库标题
- _search_bgm_from_library 直接调用：类型过滤 + 去重 + 按分数排序
"""

from __future__ import annotations

import pytest

from clipwright.agents.audio_agent import AudioAgent, _search_bgm_from_library
from clipwright.schema.agent import AgentContext, AgentDecision, AudioInput
from clipwright.schema.material import (
    MaterialAsset,
    MaterialSearchResult,
    MaterialType,
)
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track

BGM_SLOTS = {
    "intro": ["ambient"],
    "backing": ["piano"],
    "climax": ["epic"],
    "outro": ["calm"],
}


def _ctx(**extra: object) -> AgentContext:
    return AgentContext(
        pipeline_id="p_lib_bgm",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
        extra_params={**extra},
    )


def _bgm_timeline() -> Timeline:
    """单个 BGM clip：0s → intro 槽位，总长 100s。"""
    tl = Timeline(duration_sec=100.0)
    tl.tracks.append(Track(
        id="a_bgm",
        name="BGM",
        kind=ClipKind.AUDIO,
        index=0,
        clips=[
            Clip(id="bgm1", kind=ClipKind.AUDIO, asset_id="/bgm1.mp3",
                 track_id="a_bgm", start_sec=0.0, duration_sec=15.0),
        ],
    ))
    return tl


def _empty_audio_timeline() -> Timeline:
    tl = Timeline(duration_sec=60.0)
    tl.tracks.append(Track(
        id="a_bgm",
        name="BGM",
        kind=ClipKind.AUDIO,
        index=0,
        clips=[],
    ))
    return tl


def _audio_input(tl: Timeline) -> AudioInput:
    return AudioInput(
        context=_ctx(),
        timeline=tl,
        audio_config={"bgm_slots": BGM_SLOTS},
    )


def _audio_asset(
    aid: str,
    title: str,
    url: str | None = None,
    local_path: str | None = None,
) -> MaterialAsset:
    return MaterialAsset(
        id=aid,
        title=title,
        type=MaterialType.AUDIO,
        url=url,
        local_path=local_path,
    )


def _search_result(asset: MaterialAsset, score: float = 0.9) -> MaterialSearchResult:
    return MaterialSearchResult(asset=asset, score=score, source_name="test_source")


def _mock_registry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_sources: bool = True,
    results: list[MaterialSearchResult] | None = None,
) -> None:
    """把 MaterialRegistry.list/search 替换为固定桩。"""
    monkeypatch.setattr(
        "clipwright.agents.audio_agent.MaterialRegistry.list",
        lambda: [{"id": "s1", "name": "test"}] if has_sources else [],
    )

    async def _fake_search(
        query: str,
        top_k_per_source: int = 10,
        source_ids: list[str] | None = None,
    ) -> list[MaterialSearchResult]:
        return results if results is not None else []

    monkeypatch.setattr(
        "clipwright.agents.audio_agent.MaterialRegistry.search",
        _fake_search,
    )


# ── 素材库音频 → 填充 BGM ──

@pytest.mark.asyncio
async def test_library_audio_fills_bgm_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """素材库有 AUDIO 素材 → clip 元数据填充 bgm_library/bgm_style + note。"""
    asset = _audio_asset("a1", "舒缓钢琴曲", url="https://cdn.example.com/piano.mp3")
    _mock_registry(monkeypatch, results=[_search_result(asset)])
    agent = AudioAgent()
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clip = out.timeline.tracks[0].clips[0]
    assert clip.metadata["bgm_library"]["title"] == "舒缓钢琴曲"
    assert clip.metadata["bgm_library"]["url"] == "https://cdn.example.com/piano.mp3"
    assert clip.metadata["bgm_style"] == "舒缓钢琴曲"
    # 槽位规则仍照常计算（不回归）
    assert clip.metadata["bgm_slot"] == "intro"
    assert clip.metadata["bpm"] == 120
    assert any(
        "BGM 来自素材库" in n and "舒缓钢琴曲" in n for n in out.audio_notes
    )


@pytest.mark.asyncio
async def test_library_audio_uses_local_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """素材库素材只有 local_path（无 url）→ bgm_library 携带 local_path。"""
    asset = _audio_asset("a2", "Piano Loop", local_path="/cache/music/piano.mp3")
    _mock_registry(monkeypatch, results=[_search_result(asset)])
    agent = AudioAgent()
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    clip = out.timeline.tracks[0].clips[0]
    assert clip.metadata["bgm_library"]["local_path"] == "/cache/music/piano.mp3"
    assert clip.metadata["bgm_library"]["url"] is None


# ── 回退路径（空素材库 / 无音频 / 异常）──

@pytest.mark.asyncio
async def test_empty_library_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """MaterialRegistry.list() 为空 → 行为与基线完全一致。"""
    _mock_registry(monkeypatch, has_sources=False)
    agent = AudioAgent()
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clip = out.timeline.tracks[0].clips[0]
    assert "bgm_library" not in clip.metadata
    assert "bgm_style" not in clip.metadata
    assert clip.metadata["bgm_slot"] == "intro"
    assert clip.metadata["bpm"] == 120
    assert clip.volume == 1.0  # B6: 不再硬压 0.3，改走 audio_fade 曲线
    assert clip.audio_fade_in_sec == 1.0
    assert not any("BGM 来自素材库" in n for n in out.audio_notes)


@pytest.mark.asyncio
async def test_no_audio_results_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """素材库只有 VIDEO 素材 → 回退原有 bgm_slots 行为，无任何素材库注入。"""
    video = MaterialAsset(
        id="v1", title="city", type=MaterialType.VIDEO,
        url="https://cdn.example.com/v.mp4",
    )
    _mock_registry(monkeypatch, results=[_search_result(video, 0.95)])
    agent = AudioAgent()
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clip = out.timeline.tracks[0].clips[0]
    assert "bgm_library" not in clip.metadata
    assert "bgm_style" not in clip.metadata
    assert clip.metadata["bgm_slot"] == "intro"
    assert not any("BGM 来自素材库" in n for n in out.audio_notes)


@pytest.mark.asyncio
async def test_search_exception_falls_back_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search 抛异常 → 不崩溃，静默回退原有行为。"""
    monkeypatch.setattr(
        "clipwright.agents.audio_agent.MaterialRegistry.list",
        lambda: [{"id": "s1", "name": "test"}],
    )

    async def _boom(
        query: str,
        top_k_per_source: int = 10,
        source_ids: list[str] | None = None,
    ) -> list[MaterialSearchResult]:
        raise RuntimeError("search down")

    monkeypatch.setattr(
        "clipwright.agents.audio_agent.MaterialRegistry.search",
        _boom,
    )
    agent = AudioAgent()
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clip = out.timeline.tracks[0].clips[0]
    assert "bgm_library" not in clip.metadata
    assert "bgm_style" not in clip.metadata
    assert clip.metadata["bgm_slot"] == "intro"
    assert not any("BGM 来自素材库" in n for n in out.audio_notes)


@pytest.mark.asyncio
async def test_no_audio_clips_uses_library_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无音频 clip 且素材库有音频 → 占位建议 BGM 用素材库标题。"""
    asset = _audio_asset("a1", "LibraryAmbient", url="https://cdn.example.com/amb.mp3")
    _mock_registry(monkeypatch, results=[_search_result(asset)])
    agent = AudioAgent()
    inp = _audio_input(_empty_audio_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    joined = "\n".join(out.audio_notes)
    assert "BGM 来自素材库: LibraryAmbient" in joined
    assert "建议 BGM: LibraryAmbient" in joined or "素材库 BGM 已入轨: 1 段" in joined
    # C1: 素材库 BGM 真实入轨（原实现只写"建议"note，render 永远混不到 BGM）
    audio_track = next(t for t in inp.timeline.tracks if t.kind == ClipKind.AUDIO)
    bgm_clips = [c for c in audio_track.clips if "amb.mp3" in (c.asset_id or "")]
    assert len(bgm_clips) == 1
    assert bgm_clips[0].volume == 0.25


# ── helper 直接调用 ──

@pytest.mark.asyncio
async def test_helper_filters_audio_dedups_sorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_search_bgm_from_library：过滤非音频、按 id 去重、按分数降序。"""
    audio_a = _audio_asset("a1", "piano", url="https://x/p.mp3")
    audio_b = _audio_asset("a2", "piano dup", url="https://x/p2.mp3")
    video = MaterialAsset(
        id="v1", title="clip", type=MaterialType.VIDEO,
        url="https://x/v.mp4",
    )
    _mock_registry(monkeypatch, results=[
        _search_result(video, 0.95),
        _search_result(audio_b, 0.8),
        _search_result(audio_a, 0.9),
    ])

    out = await _search_bgm_from_library(BGM_SLOTS, top_k=3)

    assert [o["asset"].id for o in out] == ["a1", "a2"]
    assert out[0]["score"] == 0.9


@pytest.mark.asyncio
async def test_helper_empty_sources_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """素材库无来源 → helper 直接返回 []（不调用 search）。"""
    _mock_registry(monkeypatch, has_sources=False)
    out = await _search_bgm_from_library(BGM_SLOTS)
    assert out == []
