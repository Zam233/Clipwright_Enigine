"""NEL（Narration Event Line）单元测试 — Phase 2.3-2.5 回归。"""

from __future__ import annotations

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.narration_events import (
    align_animations_to_nel,
    extract_nel,
    pick_nel_event,
    snap_to_beat,
)


def _seg(text: str, start: float, dur: float = 3.0) -> dict:
    """构造带实测时间的配音分段（char_timings 均匀近似）。"""
    n = max(1, len(text))
    return {
        "text": text,
        "start_sec": start,
        "end_sec": start + dur,
        "char_timings": [round(start + dur * (i + 0.5) / n, 3) for i in range(n)],
    }


def test_extract_nel_numbers_and_emphasis():
    segs = [
        _seg("公司营收同比增长 300%", 0.0),
        _seg("这是一个「历史性突破」！", 3.5),
    ]
    events = extract_nel(segs)
    types = [e["type"] for e in events]
    assert "number" in types
    assert "emphasis" in types
    # 事件时间落在对应段窗口内
    for ev in events:
        if ev["type"] == "number":
            assert 0.0 <= ev["t"] <= 3.0
        if ev["type"] == "emphasis":
            assert 3.5 <= ev["t"] <= 6.5


def test_extract_nel_turn_and_question():
    segs = [_seg("但是我们没有预料到风险", 1.0), _seg("为什么会出现这种情况？", 5.0)]
    types = [e["type"] for e in extract_nel(segs)]
    assert "turn" in types
    assert "question" in types


def test_extract_nel_sorted_and_capped():
    segs = [_seg("第一 100 万，第二 200 万，第三 300 万", 0.0)]
    events = extract_nel(segs, max_per_type=2)
    times = [e["t"] for e in events]
    assert times == sorted(times)
    assert sum(1 for e in events if e["type"] == "number") <= 2


def test_pick_nel_event_prefers_number():
    nel = [
        {"t": 2.0, "type": "enum", "payload": "第一", "text": ""},
        {"t": 2.5, "type": "number", "payload": "300%", "text": ""},
    ]
    ev = pick_nel_event(nel, 2.0, 5.0)
    assert ev is not None and ev["type"] == "number"


def test_pick_nel_event_outside_window_or_too_far():
    nel = [{"t": 9.0, "type": "number", "payload": "99%", "text": ""}]
    assert pick_nel_event(nel, 2.0, 5.0) is None
    assert pick_nel_event(nel, 2.0, 12.0, max_shift=1.0) is None  # 事件距窗口起点过远


def test_snap_to_beat():
    # BPM=120 → 拍间隔 0.5s
    assert snap_to_beat(0.49, 120) == 0.5
    assert snap_to_beat(0.1, 120) == 0.0  # 距 0.0 拍 0.1s ≤ 0.25，吸附
    assert snap_to_beat(0.2, 120, max_shift=0.1) == 0.2  # 偏差 0.2 > 0.1，不吸
    assert snap_to_beat(1.0, None) == 1.0


def test_align_animations_to_nel_postpass():
    """后置对齐：MG clip 吸附到窗口内 NEL 事件；无事件时 BPM 落拍。"""
    timeline = Timeline(
        id="t1", width=1920, height=1080, fps=30, duration_sec=12,
        tracks=[
            Track(id="a_narr", name="旁白", kind=ClipKind.AUDIO, index=0, metadata={
                "nel": [{"t": 2.4, "type": "number", "payload": "300%", "text": ""}],
                "bpm": 120.0,
            }),
            Track(id="anim", name="动画", kind=ClipKind.ANIMATION, index=1, clips=[
                Clip(id="mg1", kind=ClipKind.ANIMATION, asset_id="", track_id="anim",
                     start_sec=2.0, duration_sec=3.0,
                     metadata={"renderer": "mg_hyperframes"}),
                Clip(id="mg2", kind=ClipKind.ANIMATION, asset_id="", track_id="anim",
                     start_sec=5.2, duration_sec=3.0,
                     metadata={"renderer": "mg_hyperframes"}),
            ]),
        ],
    )
    stats = align_animations_to_nel(timeline)
    assert stats["aligned"] == 1
    assert stats["beat_snapped"] == 1
    mg1 = timeline.tracks[1].clips[0]
    assert mg1.start_sec == 2.4
    assert mg1.metadata.get("nel_aligned") is True
    assert mg1.metadata.get("nel_cue") == "300%"
    mg2 = timeline.tracks[1].clips[1]
    assert mg2.metadata.get("beat_snapped") is True
    assert mg2.start_sec == 5.0  # 5.2 → 吸附到 0.5s 拍网格


def test_align_noop_without_metadata():
    timeline = Timeline(
        id="t2", width=1920, height=1080, fps=30, duration_sec=6,
        tracks=[Track(id="anim", name="动画", kind=ClipKind.ANIMATION, index=0, clips=[
            Clip(id="mg1", kind=ClipKind.ANIMATION, asset_id="", track_id="anim",
                 start_sec=1.0, duration_sec=2.0,
                 metadata={"renderer": "mg_hyperframes"}),
        ])],
    )
    assert align_animations_to_nel(timeline) == {"aligned": 0, "beat_snapped": 0}
