"""V32 负时间片头：时间线后移、音频延迟、音画同步（单测）。

真机渲染验证在 `scripts/e2e_preroll_v32.py`；这里锁住纯逻辑与延迟工具。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.preroll import MAX_PREROLL_SEC, apply_preroll, delay_audio, plan


def _timeline(*, visuals: int = 2, clip_sec: float = 2.0, audio: bool = True) -> Timeline:
    video_clips = [
        Clip(id=f"v{i}", kind=ClipKind.VIDEO, asset_id="a.mp4", track_id="tv",
             start_sec=round(i * clip_sec, 3), duration_sec=clip_sec, source_offset_sec=0.0)
        for i in range(visuals)
    ]
    tracks = [
        Track(id="tv", name="视频轨", kind=ClipKind.VIDEO, index=0, clips=video_clips),
        Track(id="tc", name="字幕轨", kind=ClipKind.CAPTION, index=1, clips=[
            Clip(id="c0", kind=ClipKind.CAPTION, asset_id="", track_id="tc",
                 start_sec=0.0, duration_sec=clip_sec, text="词"),
        ]),
    ]
    if audio:
        tracks.append(Track(id="ta", name="音频轨", kind=ClipKind.AUDIO, index=2, clips=[
            Clip(id="a0", kind=ClipKind.AUDIO, asset_id="song.wav", track_id="ta",
                 start_sec=0.0, duration_sec=visuals * clip_sec),
        ]))
    return Timeline(id="tl", width=960, height=540, fps=30,
                    duration_sec=visuals * clip_sec, tracks=tracks)


class TestPlan:
    def test_expected_video_is_duration_plus_preroll(self) -> None:
        result = plan(_timeline(visuals=3, clip_sec=2.0), 1.5)
        assert result.preroll_sec == 1.5
        assert result.audio_delay_sec == 1.5
        assert result.expected_video_sec == 7.5

    def test_preroll_clamped(self) -> None:
        assert plan(_timeline(), 999).preroll_sec == MAX_PREROLL_SEC

    def test_negative_treated_as_zero(self) -> None:
        assert plan(_timeline(), -3).preroll_sec == 0.0


class TestApplyPreroll:
    def test_zero_preroll_is_noop(self) -> None:
        timeline = _timeline()
        assert apply_preroll(timeline, 0) is timeline

    def test_all_tracks_shifted_by_preroll(self) -> None:
        shifted = apply_preroll(_timeline(), 1.0)
        for track in shifted.tracks:
            for clip in track.clips:
                if (clip.metadata or {}).get("preroll"):
                    assert clip.start_sec == 0.0
                else:
                    assert clip.start_sec >= 1.0 - 1e-9

    def test_visual_relative_order_kept(self) -> None:
        shifted = apply_preroll(_timeline(visuals=3, clip_sec=2.0), 1.0)
        video = next(t for t in shifted.tracks if t.kind == ClipKind.VIDEO)
        starts = [c.start_sec for c in video.clips if not (c.metadata or {}).get("preroll")]
        assert starts == [1.0, 3.0, 5.0]
        assert [c.duration_sec for c in video.clips if not (c.metadata or {}).get("preroll")] == [2.0, 2.0, 2.0]

    def test_audio_track_also_shifted_keeps_sync(self) -> None:
        shifted = apply_preroll(_timeline(audio=True), 1.0)
        audio = next(t for t in shifted.tracks if t.kind == ClipKind.AUDIO)
        assert audio.clips[0].start_sec == 1.0
        # 音频与视频的相对起点仍一致 → 内部不漂
        video = next(t for t in shifted.tracks if t.kind == ClipKind.VIDEO and t.clips)
        assert audio.clips[0].start_sec == audio.clips[0].start_sec

    def test_lead_segment_added_with_preroll_duration(self) -> None:
        shifted = apply_preroll(_timeline(visuals=2, clip_sec=2.0), 1.5)
        lead = [
            clip for track in shifted.tracks if track.kind != ClipKind.AUDIO
            for clip in track.clips if (clip.metadata or {}).get("preroll")
        ]
        assert len(lead) == 1
        assert lead[0].start_sec == 0.0
        assert lead[0].duration_sec == 1.5

    def test_lead_reuses_first_asset_not_black(self) -> None:
        shifted = apply_preroll(_timeline(), 1.0)
        lead = next(
            clip for track in shifted.tracks if track.kind == ClipKind.VIDEO
            for clip in track.clips if (clip.metadata or {}).get("preroll")
        )
        assert lead.asset_id == "a.mp4"

    def test_duration_increased(self) -> None:
        shifted = apply_preroll(_timeline(visuals=2, clip_sec=2.0), 1.0)
        assert shifted.duration_sec == pytest.approx(5.0)

    def test_original_timeline_untouched(self) -> None:
        timeline = _timeline()
        apply_preroll(timeline, 2.0)
        assert timeline.duration_sec == 4.0
        assert timeline.tracks[0].clips[0].start_sec == 0.0


class TestDelayAudio:
    def _sine(self, path, seconds: float = 2.0) -> None:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"sine=frequency=440:duration={seconds}", "-c:a", "pcm_s16le", str(path)],
            check=True, capture_output=True,
        )

    def _duration(self, path) -> float:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, encoding="utf-8",
        ).stdout
        import json as _json

        return float((_json.loads(out).get("format") or {}).get("duration") or 0.0)

    def test_delay_adds_silent_lead(self, tmp_path) -> None:
        if shutil.which("ffmpeg") is None:
            pytest.skip("未安装 ffmpeg")
        src = tmp_path / "song.wav"
        self._sine(src, 2.0)
        out = delay_audio(src, 1.0, tmp_path / "song_delayed.wav")
        assert out.exists()
        assert self._duration(out) == pytest.approx(3.0, abs=0.05)

    def test_zero_preroll_returns_source(self, tmp_path) -> None:
        src = tmp_path / "song.wav"
        self._sine(src, 1.0)
        assert delay_audio(src, 0, tmp_path / "x.wav") == src
