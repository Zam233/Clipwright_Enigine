"""V25 逐词误差：检测器精度与 `init` 待确认清单两条路。

- A 路（实测起音）：合成点击轨（真值精确）→ `detect_audio_events` 检出的 onset 与真值
  误差应 ≤42ms，且**不能漏检**紧跟上一声的起音（旧实现回看 0.3s，120BPM 会漏拍）。
- B 路（无实测数据）：cues 全部标 `init`，并逐条进 `pending_confirmation()`。
"""

from __future__ import annotations

import numpy as np

from clipwright.schema.lyrics import LyricLine, LyricWord, LyricsDoc
from clipwright.services.content_audio import detect_audio_events
from clipwright.services.mv_cues import CuesDoc, build_grid, cues_from_lyrics

SAMPLE_RATE = 22050
TOLERANCE_SEC = 0.042


def _click_track(onsets: list[float], duration: float) -> np.ndarray:
    total = int(duration * SAMPLE_RATE)
    signal = np.zeros(total, dtype=np.float32)
    width = int(0.012 * SAMPLE_RATE)
    for start in onsets:
        i0 = int(round(start * SAMPLE_RATE))
        local = np.arange(width) / SAMPLE_RATE
        signal[i0:i0 + width] += (
            0.9 * np.sin(2 * np.pi * 1000 * local) * np.exp(-local * 80)
        ).astype(np.float32)
    return signal


def _detected(signal: np.ndarray) -> list[float]:
    events = detect_audio_events(signal, SAMPLE_RATE)
    return sorted(float(ev["t0"]) for ev in events if ev.get("kind") == "onset")


class TestOnsetAccuracy:
    def test_all_clicks_detected_within_tolerance(self) -> None:
        truth = [0.2, 0.7, 1.2, 1.7, 2.2]
        detected = _detected(_click_track(truth, 2.6))
        assert len(detected) == len(truth), f"漏检：{detected}"
        for t in truth:
            nearest = min(detected, key=lambda d: abs(d - t))
            assert abs(nearest - t) <= TOLERANCE_SEC, (t, nearest)

    def test_back_to_back_onsets_not_missed(self) -> None:
        """0.3s 回看会把"上一声之后 0.3s 内"的起音吞掉——V25 的根因，这里钉住。"""
        truth = [0.5, 0.8, 1.1, 1.4]  # 间隔 0.3s，旧实现必漏
        detected = _detected(_click_track(truth, 1.8))
        matched = [
            t for t in truth
            if any(abs(d - t) <= TOLERANCE_SEC for d in detected)
        ]
        assert len(matched) == len(truth), f"只检出 {matched}"

    def test_no_duplicate_onsets_within_50ms(self) -> None:
        detected = _detected(_click_track([0.3], 1.0))
        assert len(detected) <= 2
        for a, b in zip(detected, detected[1:]):
            assert b - a >= 0.05

    def test_silence_has_no_onsets(self) -> None:
        assert _detected(np.zeros(int(SAMPLE_RATE * 1.0), dtype=np.float32)) == []


class TestInitPending:
    def _cues(self, *, onsets=None) -> CuesDoc:
        lyrics = LyricsDoc(lines=[
            LyricLine(i=0, t0=0.5, t1=2.0, text_ref="text(0)",
                      words=[LyricWord(w=0, t0=0.5, t1=1.0, key=True)],
                      syllables=[0.5, 0.9]),
        ])
        grid = build_grid(120.0, beat_zero_sec=0.0)
        return CuesDoc(grid=grid, items=cues_from_lyrics(lyrics, grid=grid, onsets=onsets))

    def test_all_init_without_onset_data(self) -> None:
        doc = self._cues()
        assert doc.init_count() == len(doc.items) > 0

    def test_pending_list_lists_every_init_cue(self) -> None:
        doc = self._cues()
        pending = doc.pending_confirmation()
        assert len(pending) == len(doc.items)
        assert {item["name"] for item in pending} == {cue.name for cue in doc.items}
        assert all(item["reason"] for item in pending)

    def test_measured_onsets_clear_pending_list(self) -> None:
        doc = self._cues(onsets=[0.5, 0.9])
        assert doc.pending_confirmation() == []
