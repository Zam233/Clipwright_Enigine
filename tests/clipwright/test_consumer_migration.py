"""M4 消费者迁移：trait_collector 把插件片段计入镜头；preroll 不为插件轨加片头。"""
from __future__ import annotations

import pytest

from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services import preroll as preroll_mod
from clipwright.services import trait_collector
from clipwright.services.clip_kind_registry import ClipKindDefinition, ClipKindRegistry

KIND = "consumer_scene"


@pytest.fixture(autouse=True)
def registry():
    ClipKindRegistry.clear()
    ClipKindRegistry.register(ClipKindDefinition(
        kind_id=KIND, owner_plugin="demo", render_contract="media_overlay.v1",
    ))
    yield
    ClipKindRegistry.clear()


def _timeline(kind: str = KIND) -> Timeline:
    return Timeline(width=320, height=240, fps=25.0, duration_sec=4.0, tracks=[
        Track(id="t1", kind=kind, index=0, clips=[Clip(
            id="c1", kind=kind, asset_id="", track_id="t1",
            start_sec=0.0, duration_sec=4.0,
        )]),
    ])


def test_trait_collector_counts_plugin_clips():
    clips = trait_collector._video_clips(_timeline())
    assert [c.id for c in clips] == ["c1"]
    assert trait_collector._video_clips(_timeline("audio")) == []


def test_preroll_shifts_plugin_track_without_lead_clip():
    result = preroll_mod.apply_preroll(_timeline(), preroll_sec=1.0)
    track = result.tracks[0]
    assert track.clips[0].start_sec == pytest.approx(1.0)
    assert all(not (c.metadata or {}).get("preroll") for c in track.clips)


def test_preroll_still_adds_lead_on_builtin_video():
    timeline = _timeline("video")
    result = preroll_mod.apply_preroll(timeline, preroll_sec=1.0)
    assert any((c.metadata or {}).get("preroll") for c in result.tracks[0].clips)
