"""场景时长必须对齐配音时间轴（E2E 2026-10-28 发现的覆盖度缺口）。

现象：知识区长片一镜到底（702.336s 配音、4203 字文稿），LLM 给出 6 个场景
各 6s，合计 36s —— 时间线只覆盖文稿 5%，画面在论证开始前就收尾了。

原因：`audio_duration_sec` 只写进了 prompt，代码里**只有"复用已确认规划书"
分支**做过等比缩放；全新生成的分支拿到什么就信什么。

修复后的契约（`_align_scene_durations`）：
- 目标长度 = dub_segments 末段（有真实分段时）否则 audio_duration_sec；
- 权重优先取口播字数（长段落镜头更长），口播全空才退回原时长比例；
- 与目标相差 ≤2% 时一个字节都不改（避免无意义抖动）。
"""

from __future__ import annotations

import pytest

from clipwright.agents.structure_agent import _align_scene_durations


def _scenes(voiceovers: list[str], durations: list[float]) -> list[dict]:
    return [
        {"title": f"s{i}", "description": "d", "duration_sec": d,
         "keywords": ["k"], "voiceover_script": v}
        for i, (v, d) in enumerate(zip(voiceovers, durations))
    ]


def test_scales_total_to_audio_duration():
    scenes = _scenes(["一二三四五六七八九十"] * 5, [6.0] * 5)

    out, warning = _align_scene_durations(scenes, audio_duration_sec=100.0)

    assert warning.startswith("场景时长已按配音时间轴缩放")
    assert sum(s["duration_sec"] for s in out) == pytest.approx(100.0, abs=0.05)


def test_weights_follow_voiceover_length():
    """口播长的场景要分到更长时长（短段落不能和长段落一样长）。"""
    scenes = _scenes(["短" * 10, "长" * 90], [6.0, 6.0])

    out, _ = _align_scene_durations(scenes, audio_duration_sec=100.0)

    short, long_ = out[0]["duration_sec"], out[1]["duration_sec"]
    assert short < long_
    assert short == pytest.approx(10.0, abs=0.2)
    assert long_ == pytest.approx(90.0, abs=0.2)


def test_falls_back_to_duration_weights_without_voiceover():
    scenes = _scenes(["", ""], [10.0, 30.0])

    out, _ = _align_scene_durations(scenes, audio_duration_sec=80.0)

    assert out[0]["duration_sec"] == pytest.approx(20.0, abs=0.2)
    assert out[1]["duration_sec"] == pytest.approx(60.0, abs=0.2)


def test_dub_segments_end_wins_over_audio_duration():
    scenes = _scenes(["a" * 10, "b" * 10], [5.0, 5.0])

    out, _ = _align_scene_durations(
        scenes, audio_duration_sec=999.0,
        dub_segments=[{"start_sec": 0, "end_sec": 3}, {"start_sec": 3, "end_sec": 20}],
    )

    assert sum(s["duration_sec"] for s in out) == pytest.approx(20.0, abs=0.05)


def test_noop_when_already_aligned():
    scenes = _scenes(["a" * 50, "b" * 50], [50.0, 50.0])

    out, warning = _align_scene_durations(scenes, audio_duration_sec=100.0)

    assert warning == ""
    assert [s["duration_sec"] for s in out] == [50.0, 50.0]


def test_noop_without_target_or_scenes():
    scenes = _scenes(["a"], [5.0])
    out, warning = _align_scene_durations(scenes, audio_duration_sec=0)
    assert out[0]["duration_sec"] == 5.0 and warning == ""

    empty, warning = _align_scene_durations([], audio_duration_sec=100)
    assert empty == [] and warning == ""
