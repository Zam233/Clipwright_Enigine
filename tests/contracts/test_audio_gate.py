"""B18：音频交付前固定清单自检。"""

from __future__ import annotations

from clipwright.agents.audio_agent import _audio_gate_problems
from clipwright.schema.timeline import Clip, Timeline, Track


def _clip(cid: str, kind: str, track_id: str, dur: float = 10.0, start: float = 0.0,
          meta: dict | None = None) -> Clip:
    return Clip(id=cid, kind=kind, asset_id="a1", track_id=track_id,
                start_sec=start, duration_sec=dur, metadata=meta or {})


def _timeline(*tracks: Track) -> Timeline:
    return Timeline(id="tl", width=1920, height=1080, fps=30, tracks=list(tracks))


def _video(dur: float = 10.0) -> Track:
    return Track(id="tv", name="视频", kind="video", index=0,
                 clips=[_clip("v1", "video", "tv", dur=dur)])


def _audio(dur: float = 10.0) -> Track:
    return Track(id="ta", name="配音", kind="audio", index=1,
                 clips=[_clip("a1", "audio", "ta", dur=dur, meta={"dubbing": True})])


def _captions(*pairs) -> Track:
    return Track(id="tc", name="字幕", kind="caption", index=2,
                 clips=[_clip(f"c{i}", "caption", "tc", dur=d, start=s)
                        for i, (s, d) in enumerate(pairs)])


def test_aligned_timeline_passes():
    tl = _timeline(_video(), _audio(), _captions((0, 4), (4, 6)))
    assert _audio_gate_problems(tl, expect_voice=True) == []


def test_voice_missing_and_end_mismatch():
    tl = _timeline(_video())
    problems = _audio_gate_problems(tl, expect_voice=True)
    assert any("没有任何配音" in p for p in problems)


def test_audio_shorter_than_timeline():
    tl = _timeline(_video(10), _audio(6))
    problems = _audio_gate_problems(tl, expect_voice=True)
    assert any("差超过 2s" in p for p in problems)


def test_caption_overlap_detected():
    tl = _timeline(_video(10), _audio(10), _captions((0, 6), (1, 2)))
    problems = _audio_gate_problems(tl)
    assert any("重叠" in p for p in problems)
