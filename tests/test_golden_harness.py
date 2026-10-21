"""C6：Golden 回归跑批（用录制跑，CI 无网络也能跑）。

一条选题 = 一份录制 + 一份基线指标。这里测的是"跑批器本身"是否可靠：
缺录制/缺基线要能被看见，指标能算出来，阈值判断沿用 golden_runs 的口径。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from clipwright.services import golden_harness as gh
from clipwright.services import golden_runs as gr
from clipwright.services import recording as rec

TIMELINE = {
    "duration_sec": 60.0,
    "tracks": [
        {"kind": "video", "clips": [
            {"id": "c1", "kind": "video", "start_sec": 0, "duration_sec": 30,
             "transition": "fade"},
            {"id": "c2", "kind": "image", "start_sec": 30, "duration_sec": 30},
        ]},
        {"kind": "subtitle", "clips": [
            {"id": "s1", "kind": "subtitle", "start_sec": 0, "duration_sec": 60},
        ]},
        {"kind": "audio", "clips": [
            {"id": "a1", "kind": "audio", "start_sec": 0, "duration_sec": 60},
        ]},
    ],
}


def _write_recording(path, *, topic: str) -> None:
    """录一份"可被回放驱动"的最小录制：plan + 一步工具结果（带 timeline）。"""
    from clipwright.schema.task import Plan, PlanStep

    plan = Plan(steps=[PlanStep(id="s1", title="整片生成", capability="run_pipeline",
                                kind="pipeline", args={})])
    recorder = rec.Recorder(path)
    recorder.record("llm", "plan", {"topic": topic}, plan.model_dump(mode="json"))
    recorder.record("tool", "run_pipeline", {"step_id": "s1", "args": {}},
                    {"status": "ok", "output": {"tracks": 3, "clips": 4,
                                                "duration_sec": 60.0},
                     "timeline": TIMELINE})
    recorder.record("llm", "reflect", {"step_id": "s1", "status": "done"},
                    {"action": "continue"})


@pytest.fixture()
def workspace(tmp_path):
    topics_file = tmp_path / "topics.json"
    topics_file.write_text(json.dumps({"topics": [
        {"id": "g1", "topic": "选题一", "duration_sec": 60,
         "category_plugin_id": "knowledge_long"},
    ]}), encoding="utf-8")
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    _write_recording(recordings / "g1.jsonl", topic="选题一")

    baselines_file = tmp_path / "baselines.json"
    baselines_file.write_text(json.dumps({"baselines": [
        {"topic_id": "g1", "recording": "g1.jsonl",
         "metrics": {"clips": 4, "tracks": 3, "duration_sec": 60.0,
                     "subtitles": 1, "cost_usd": 1.0}},
    ]}), encoding="utf-8")
    return {
        "topics": topics_file,
        "baselines": baselines_file,
        "recordings": recordings,
        "tasks": tmp_path / "tasks",
    }


def test_run_all_passes_with_matching_baseline(workspace):
    report = asyncio.run(gh.run_all(
        topics_file=workspace["topics"],
        baselines_file=workspace["baselines"],
        recordings_dir=workspace["recordings"],
        checkpoint_dir=workspace["tasks"],
    ))
    assert report.passed, report.to_public()
    assert report.runs[0].metrics["clips"] == 4
    assert report.missing_baselines == []


def test_missing_recording_is_reported_not_crashed(workspace):
    (workspace["recordings"] / "g1.jsonl").unlink()
    report = asyncio.run(gh.run_all(
        topics_file=workspace["topics"],
        baselines_file=workspace["baselines"],
        recordings_dir=workspace["recordings"],
        checkpoint_dir=workspace["tasks"],
    ))
    assert not report.passed
    assert "缺录制" in report.runs[0].error


def test_missing_baseline_blocks_pass(workspace):
    workspace["baselines"].write_text(json.dumps({"baselines": []}), encoding="utf-8")
    report = asyncio.run(gh.run_all(
        topics_file=workspace["topics"],
        baselines_file=workspace["baselines"],
        recordings_dir=workspace["recordings"],
        checkpoint_dir=workspace["tasks"],
    ))
    assert not report.passed
    assert report.missing_baselines == ["g1"]


def test_quality_regression_fails(workspace):
    """产物缩水 50% → 必须红，而不是"看起来差不多"。"""
    baseline = json.loads(workspace["baselines"].read_text(encoding="utf-8"))
    baseline["baselines"][0]["metrics"]["clips"] = 12   # 基线 12，实际 4 → -66%
    workspace["baselines"].write_text(json.dumps(baseline), encoding="utf-8")

    report = asyncio.run(gh.run_all(
        topics_file=workspace["topics"],
        baselines_file=workspace["baselines"],
        recordings_dir=workspace["recordings"],
        checkpoint_dir=workspace["tasks"],
    ))
    assert not report.passed
    assert any("clips" in f for f in report.runs[0].failures)


def test_report_markdown_shape(workspace):
    report = asyncio.run(gh.run_all(
        topics_file=workspace["topics"],
        baselines_file=workspace["baselines"],
        recordings_dir=workspace["recordings"],
        checkpoint_dir=workspace["tasks"],
    ))
    md = report.to_markdown()
    assert md.startswith("# Golden 回归报告")
    assert "结论：通过" in md
    assert "| g1 | ✅" in md


def test_metrics_fall_back_to_output_tracks():
    """录制里没有 timeline 形状时，退而用 output 里的 tracks/clips（C3 能力形状）。"""
    path = __import__("tempfile").NamedTemporaryFile(suffix=".jsonl", delete=False).name
    recorder = rec.Recorder(path)
    recorder.record("tool", "run_pipeline", {"step_id": "s1", "args": {}},
                    {"status": "ok", "output": {"tracks": [
                        {"kind": "video", "clips": [{"id": "c1"}, {"id": "c2"}]}]}})
    metrics = gh._metrics_from_recording(rec.Replayer(path))  # noqa: SLF001
    assert metrics.get("clips") == 2
