"""歌词与拍点服务单测（PRD C5/C7、R-11/R-13、验收 V24/V25）。"""

from __future__ import annotations

import pytest

from clipwright.schema.lyrics import LyricLine, LyricsDoc, LyricWord, parse_lyrics
from clipwright.services.mv_cues import (
    BeatGrid,
    build_cues,
    build_grid,
    beat_positions,
    grid_hit_rate,
    load_cues,
    make_cue,
    pick_nearest_onset,
    save_cues,
)


def _lyrics() -> LyricsDoc:
    return LyricsDoc(
        language="en",
        lines=[
            LyricLine(
                i=0, t0=0.9, t1=3.0, text_ref="text(0)",
                words=[LyricWord(w=0, t0=0.9, t1=1.4, key=True), LyricWord(w=1, t0=1.4, t1=2.2)],
            ),
            LyricLine(
                i=1, t0=3.2, t1=6.0, text_ref="text(1)",
                syllables=[4.1, 4.33, 4.56],
            ),
        ],
    )


class TestBeatGrid:
    def test_beat_sec_from_bpm(self) -> None:
        grid = build_grid(130, 0.344)
        assert grid.beat_sec == pytest.approx(60 / 130)
        assert grid.bar_sec == pytest.approx(4 * 60 / 130)

    def test_beat_index_and_time_round_trip(self) -> None:
        grid = build_grid(130, 0.344)
        t = grid.time_of_beat(10)
        assert grid.beat_index(t) == pytest.approx(10.0)

    def test_snap_beat_and_half(self) -> None:
        grid = build_grid(120, 0.0)  # 一拍 0.5s
        assert grid.snap(0.26, "beat") == pytest.approx(0.5)
        assert grid.snap(0.26, "half") == pytest.approx(0.25)  # 半拍 0.25s，比 0 更近
        assert grid.snap(1.30, "bar") == pytest.approx(2.0)

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(ValueError):
            build_grid(120).snap(0.1, "third")

    def test_zero_bpm_is_identity(self) -> None:
        grid = build_grid(0)
        assert grid.snap(1.234) == 1.234

    def test_error_frames(self) -> None:
        grid = build_grid(120, 0.0)
        assert grid.error_frames(0.52, fps=50) == pytest.approx(1.0)
        assert grid.is_on_grid(0.505, fps=60, tolerance_frames=1.0)

    def test_beat_positions_within_duration(self) -> None:
        grid = build_grid(120, 0.0)
        positions = beat_positions(grid, 2.0)
        assert positions[:3] == [0.0, 0.5, 1.0]
        assert all(0 <= p <= 2.0 for p in positions)


class TestCues:
    def test_measured_onset_wins_over_grid(self) -> None:
        grid = build_grid(120, 0.0)
        cue = make_cue("x", 0.30, grid=grid, onsets=[0.29])
        assert cue.source == "measured"
        assert cue.t == pytest.approx(0.29)

    def test_init_when_no_onset_snaps_to_grid(self) -> None:
        grid = build_grid(120, 0.0)
        cue = make_cue("x", 0.62, grid=grid, unit="beat")
        assert cue.source == "init"
        assert cue.t == pytest.approx(0.5)

    def test_onset_outside_window_ignored(self) -> None:
        assert pick_nearest_onset(1.0, [1.5], window=0.2) is None
        assert pick_nearest_onset(1.0, [1.1], window=0.2) == 1.1

    def test_cues_from_lyrics_includes_lines_keywords_syllables(self) -> None:
        doc = build_cues(_lyrics(), bpm=120, beat_zero_sec=0.0)
        names = [item.name for item in doc.items]
        assert "line_0" in names and "line_1" in names
        assert "key_0_0" in names
        assert sum(1 for n in names if n.startswith("syl_1_")) == 3

    def test_extra_names_appended(self) -> None:
        doc = build_cues(_lyrics(), bpm=120, extra_names=[("chorus_in", 6.1, "bar")])
        assert doc.by_name("chorus_in") is not None

    def test_init_count_tracks_untested_times(self) -> None:
        doc = build_cues(_lyrics(), bpm=120)
        assert doc.init_count() == len(doc.items)

    def test_round_trip_json(self, tmp_path) -> None:
        doc = build_cues(_lyrics(), bpm=130, beat_zero_sec=0.344)
        path = save_cues(tmp_path / "cues.json", doc)
        loaded = load_cues(path)
        assert loaded.grid.bpm == 130
        assert [c.name for c in loaded.items] == [c.name for c in doc.items]
        assert loaded.grid.beat_sec == pytest.approx(doc.grid.beat_sec, rel=1e-6)

    def test_saved_json_has_measured_flags(self, tmp_path) -> None:
        doc = build_cues(_lyrics(), bpm=120)
        path = save_cues(tmp_path / "cues.json", doc)
        text = path.read_text(encoding="utf-8")
        assert '"source": "init"' in text


class TestGridHitRate:
    def test_all_on_grid(self) -> None:
        grid = build_grid(120, 0.0)
        result = grid_hit_rate([0.0, 0.5, 1.0, 1.5], grid, fps=60)
        assert result["rate"] == 1.0
        assert result["worst_frames"] == 0.0

    def test_off_grid_reduces_rate(self) -> None:
        grid = build_grid(120, 0.0)
        result = grid_hit_rate([0.0, 0.51, 1.02], grid, fps=60)
        # 0.51 → 0.6 帧误差（命中）；1.02 → 1.2 帧误差（未命中）
        assert result["hits"] == 2
        assert result["rate"] == pytest.approx(2 / 3)

    def test_empty_is_perfect(self) -> None:
        assert grid_hit_rate([], build_grid(120))["rate"] == 1.0


class TestLyricsDoc:
    def test_overlap_rejected(self) -> None:
        with pytest.raises(Exception):
            LyricsDoc(lines=[LyricLine(i=0, t0=0, t1=5), LyricLine(i=1, t0=4, t1=8)])

    def test_reversed_line_rejected(self) -> None:
        with pytest.raises(Exception):
            LyricsDoc(lines=[LyricLine(i=0, t0=5, t1=4)])

    def test_line_at_and_band_ref(self) -> None:
        doc = _lyrics()
        assert doc.line_at(1.0) is not None
        assert doc.band_text_ref(1.0) == "text(0)"
        assert doc.band_text_ref(3.1) is None

    def test_keyword_flag_and_keywords(self) -> None:
        doc = _lyrics()
        assert [word.w for word in doc.lines[0].keywords()] == [0]

    def test_parse_from_dict(self) -> None:
        doc = parse_lyrics({"lines": [{"i": 0, "t0": 0, "t1": 1, "text_ref": "text(0)"}]})
        assert doc.lines[0].duration == 1.0

    def test_band_false_suppresses_ref(self) -> None:
        doc = LyricsDoc(lines=[LyricLine(i=0, t0=0, t1=2, band=False, text_ref="text(0)")])
        assert doc.band_text_ref(0.5) is None
