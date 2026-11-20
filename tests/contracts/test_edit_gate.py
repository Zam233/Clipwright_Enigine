"""B17：剪辑交付前固定清单自检。"""

from __future__ import annotations

from clipwright.agents.edit_agent import _edit_gate_problems
from clipwright.schema.timeline import Clip, Timeline, Track


def _clip(cid: str, kind: str, track_id: str, dur: float = 5.0, start: float = 0.0) -> Clip:
    return Clip(id=cid, kind=kind, asset_id="a1", track_id=track_id,
                start_sec=start, duration_sec=dur)


def _timeline(*tracks: Track) -> Timeline:
    return Timeline(id="tl", width=1920, height=1080, fps=30, tracks=list(tracks))


def _video_track(clips=None) -> Track:
    return Track(id="tv", name="视频", kind="video", index=0,
                 clips=list(clips if clips is not None else [_clip("c1", "video", "tv")]))


def test_valid_timeline_passes():
    assert _edit_gate_problems(_timeline(_video_track())) == []


def test_no_tracks_or_no_video():
    assert _edit_gate_problems(_timeline()) == ["时间线没有任何轨道"]
    problems = _edit_gate_problems(_timeline(Track(id="ta", name="音频", kind="audio", index=1)))
    assert any("没有视频片段" in p for p in problems)


def test_duration_deviation_flagged():
    tl = _timeline(_video_track([_clip("c1", "video", "tv", dur=5.0)]))
    problems = _edit_gate_problems(tl, target_sec=20.0)
    assert any("偏差过大" in p for p in problems)


def test_audio_required_in_narration_mode():
    tl = _timeline(_video_track())
    problems = _edit_gate_problems(tl, has_audio=True)
    assert any("音频轨为空" in p for p in problems)

    audio = Track(id="ta", name="音频", kind="audio", index=1,
                  clips=[_clip("a1", "audio", "ta")])
    assert _edit_gate_problems(_timeline(_video_track(), audio), has_audio=True) == []
