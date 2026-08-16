"""P8: 节拍对齐剪辑 beat-sync — 场景起点吸附拍点测试。"""

from __future__ import annotations


def _snap(t: float, bpm: float) -> float:
    interval = 60.0 / max(bpm, 30.0)
    return round(t / interval) * interval


def test_snap_to_beat_120bpm() -> None:
    """120 BPM → 拍间隔 0.5s；0.7 → 0.5，0.9 → 1.0。"""
    assert _snap(0.7, 120) == 0.5
    assert _snap(0.9, 120) == 1.0
    assert _snap(2.05, 120) == 2.0


def test_snap_to_beat_90bpm() -> None:
    """90 BPM → 拍间隔 ~0.667s。"""
    assert _snap(0.7, 90) == 0.6666666666666666 or abs(_snap(0.7, 90) - 0.6667) < 0.01


def test_snap_invalid_bpm_floor() -> None:
    """BPM 过小 → 下限 30（间隔 2s），不除零。5s → 最近拍 4s。"""
    assert _snap(5.0, 0) == 4.0  # 60/30=2s 网格 → 5 → 4（banker's rounding）
