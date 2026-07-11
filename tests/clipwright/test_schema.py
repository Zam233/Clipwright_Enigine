"""Schema 模型测试。"""

from __future__ import annotations

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


class TestTimeline:
    def test_empty_timeline(self) -> None:
        tl = Timeline()
        assert tl.total_duration_sec == 0.0

    def test_timeline_with_clips(self) -> None:
        track = Track(
            id="v1",
            name="Video",
            kind=ClipKind.VIDEO,
            index=0,
            clips=[
                Clip(
                    id="c1",
                    kind=ClipKind.VIDEO,
                    asset_id="a1",
                    track_id="v1",
                    start_sec=0,
                    duration_sec=10,
                ),
                Clip(
                    id="c2",
                    kind=ClipKind.VIDEO,
                    asset_id="a2",
                    track_id="v1",
                    start_sec=10,
                    duration_sec=20,
                ),
            ],
        )
        tl = Timeline(tracks=[track])
        assert tl.total_duration_sec == 30.0


class TestTrack:
    def test_track_creation(self) -> None:
        track = Track(id="a1", name="Audio", kind=ClipKind.AUDIO, index=0)
        assert track.clips == []
        assert not track.locked
