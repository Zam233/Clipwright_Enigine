"""附加工具：预算/差异/时间线与音频报告。"""

from __future__ import annotations

import asyncio

from clipwright.tool.audio_inspect import AudioTimelineProbeTool, MixReportTool
from clipwright.tool.scene_analysis import SceneBudgetCheckTool, ScriptSceneDiffTool
from clipwright.tool.timeline_probe import TimelineProbeTool


def test_scene_budget_and_diff() -> None:
    scenes = [{"title": "a", "duration_sec": 5}, {"title": "b", "duration_sec": 8}]
    budget = asyncio.run(SceneBudgetCheckTool().execute(
        scenes=scenes, target_duration_sec=12,
    ))
    assert budget.output["scene_count"] == 2
    assert budget.output["total_duration_sec"] == 13
    diff = asyncio.run(ScriptSceneDiffTool().execute(
        old_scenes=scenes, new_scenes=scenes + [{"title": "c", "duration_sec": 3}],
    ))
    assert len(diff.output["added"]) == 1


def test_probe_and_mix() -> None:
    timeline = {"duration_sec": 10, "tracks": [
        {"id": "v", "kind": "video", "index": 0, "clips": [
            {"id": "v1", "kind": "video", "asset_id": "a.mp4", "track_id": "v",
             "start_sec": 0, "duration_sec": 10, "metadata": {"scene_index": 0}},
        ]},
        {"id": "a", "kind": "audio", "index": 1, "clips": [
            {"id": "a1", "kind": "audio", "asset_id": "a.mp3", "track_id": "a",
             "start_sec": 0, "duration_sec": 10, "volume": 1.5},
        ]},
    ]}
    probe = asyncio.run(TimelineProbeTool().execute(timeline=timeline, scene_index=0))
    assert probe.output["total"] == 1
    audio = asyncio.run(AudioTimelineProbeTool().execute(timeline=timeline))
    assert len(audio.output["audio"]) == 1
    mix = asyncio.run(MixReportTool().execute(timeline=timeline))
    assert mix.output["volume_over_1"] == ["a1"]
