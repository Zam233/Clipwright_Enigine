"""C6 前置：golden 录制与基线捕获工具。

三条要测的纪律：不覆盖已有基线（除非显式）、失败不留半成品录制、
录制 + 基线写完后 golden_harness 能直接跑通。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from clipwright.schema.task import Plan, PlanStep
from clipwright.services import golden_capture as gc
from clipwright.services import golden_harness as gh
from clipwright.services import golden_runs as gr


class _Planner:
    async def plan(self, task):  # noqa: ANN001, ANN201
        return Plan(steps=[PlanStep(id="s1", capability="run_pipeline", kind="pipeline",
                                    args={})])

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        return {"action": "continue"}


TIMELINE = {
    "duration_sec": 60.0,
    "tracks": [
        {"kind": "video", "clips": [{"id": "c1", "kind": "video", "start_sec": 0,
                                     "duration_sec": 60}]},
        {"kind": "subtitle", "clips": [{"id": "s1", "kind": "subtitle",
                                        "start_sec": 0, "duration_sec": 60}]},
    ],
}


async def _executor(task, step):  # noqa: ANN001, ANN201
    return {"status": "ok", "output": {"tracks": 2, "clips": 2, "duration_sec": 60.0},
            "timeline": TIMELINE}


@pytest.fixture()
def env(tmp_path):
    topics_file = tmp_path / "topics.json"
    topics_file.write_text(json.dumps({"topics": [
        {"id": "g1", "topic": "选题一", "duration_sec": 60,
         "category_plugin_id": "knowledge_long"},
    ]}), encoding="utf-8")
    return {
        "topics": topics_file,
        "recordings": tmp_path / "recordings",
        "baselines": tmp_path / "baselines.json",
        "ckpt": tmp_path / "tasks",
    }


def test_capture_writes_recording_and_baseline(env):
    topic = gr.load_topics(env["topics"])[0]
    result = asyncio.run(gc.capture_topic(
        topic,
        recordings_dir=env["recordings"],
        baselines_file=env["baselines"],
        checkpoint_dir=env["ckpt"],
        planner=_Planner(),
        executor=_executor,
    ))
    assert result.baseline_written is True
    assert result.metrics["clips"] == 2
    assert (env["recordings"] / "g1.jsonl").exists()

    baselines = gr.load_baselines(env["baselines"])
    assert baselines["g1"].recording == "g1.jsonl"
    assert baselines["g1"].metrics["clips"] == 2


def test_captured_baseline_can_be_replayed_by_harness(env):
    topic = gr.load_topics(env["topics"])[0]
    asyncio.run(gc.capture_topic(
        topic, recordings_dir=env["recordings"], baselines_file=env["baselines"],
        checkpoint_dir=env["ckpt"], planner=_Planner(), executor=_executor,
    ))
    report = asyncio.run(gh.run_all(
        topics_file=env["topics"], baselines_file=env["baselines"],
        recordings_dir=env["recordings"], checkpoint_dir=env["ckpt"] / "replay",
    ))
    assert report.passed, report.to_public()
    assert report.runs[0].metrics["clips"] == 2


def test_refuses_to_overwrite_existing_baseline(env):
    topic = gr.load_topics(env["topics"])[0]
    asyncio.run(gc.capture_topic(
        topic, recordings_dir=env["recordings"], baselines_file=env["baselines"],
        checkpoint_dir=env["ckpt"], planner=_Planner(), executor=_executor,
    ))
    with pytest.raises(gc.CaptureError, match="基线已存在|录制已存在"):
        asyncio.run(gc.capture_topic(
            topic, recordings_dir=env["recordings"], baselines_file=env["baselines"],
            checkpoint_dir=env["ckpt"], planner=_Planner(), executor=_executor,
        ))


def test_failed_task_does_not_write_baseline(env):
    """失败的任务不许被录成基线（否则回归永远"通过"）。"""
    topic = gr.load_topics(env["topics"])[0]

    async def failing(task, step):  # noqa: ANN001, ANN201
        return {"status": "error", "error": "ffmpeg 崩了"}

    with pytest.raises(gc.CaptureError, match="任务未成功|无产物指标"):
        asyncio.run(gc.capture_topic(
            topic, recordings_dir=env["recordings"], baselines_file=env["baselines"],
            checkpoint_dir=env["ckpt"], planner=_Planner(), executor=failing,
        ))
    assert not (env["baselines"]).exists()


def test_no_half_written_recording_on_failure(env):
    topic = gr.load_topics(env["topics"])[0]

    async def failing(task, step):  # noqa: ANN001, ANN201
        raise RuntimeError("执行器炸了")

    with pytest.raises(gc.CaptureError):
        asyncio.run(gc.capture_topic(
            topic, recordings_dir=env["recordings"], baselines_file=env["baselines"],
            checkpoint_dir=env["ckpt"], planner=_Planner(), executor=failing,
        ))
    assert list(env["recordings"].glob("*.jsonl")) == [], "不许留半成品录制"
