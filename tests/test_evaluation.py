"""M4：评估与回放（modern-agent PRD §2.6 / §5.5）。

可证伪点：

- 成本未知就记 unknown，绝不填 0；
- compare 给的是差值，不是"感觉变好了"；
- replay_bundle 必须同时含事件、run 结果与记忆证据，缺一项就无法复盘因果。
"""

from __future__ import annotations

import json

import pytest

from clipwright.services import evaluation


@pytest.fixture(autouse=True)
def _clean():
    evaluation.reset()
    yield
    evaluation.reset()


def test_metrics_record_and_summary():
    evaluation.record_run(task_id="t1", status="ok", turns=3, tool_calls=8,
                          cost_usd=0.12, child_runs=2)
    evaluation.record_run(task_id="t1", status="failed", turns=1, tool_calls=0)
    stat = evaluation.summary()
    assert stat["count"] == 2
    assert stat["success_rate"] == 0.5
    assert stat["cost_known"] == 1 and stat["cost_unknown_count"] == 1
    assert stat["cost_total_usd"] == 0.12


def test_unknown_cost_is_not_zero():
    row = evaluation.record_run(task_id="t2", status="ok", cost_usd=None)
    assert row["cost_usd"] is None
    assert row["cost_unknown"] is True


def test_compare_reports_delta():
    base = [{"status": "ok", "turns": 4, "tool_calls": 10, "child_runs": 1,
             "duration_ms": 100, "cost_usd": 0.2, "replans": 0}]
    curr = [{"status": "ok", "turns": 2, "tool_calls": 6, "child_runs": 2,
             "duration_ms": 80, "cost_usd": 0.15, "replans": 1}]
    out = evaluation.compare(base, curr)
    assert out["delta"]["avg_turns"]["diff"] == -2.0
    assert out["delta"]["avg_child_runs"]["diff"] == 1.0
    assert round(out["delta"]["cost_avg_usd"]["diff"], 4) == -0.05


def test_replay_bundle_contains_events_runs_and_memory():
    from clipwright.services import agent_run, memory, task_events

    evaluation.record_run(task_id="t3", status="failed", turns=1)
    task_events.clear("t3")
    task_events.emit("t3", "narration", "开始", {"x": 1})
    memory.remember("失败原因：素材缺 B-roll", task_id="t3", layer="episodic",
                    kind="failure")
    agent_run.reset()

    bundle = evaluation.replay_bundle("t3")
    assert bundle["events"], "没有事件就无法复盘因果"
    assert bundle["metrics"]
    assert bundle["memory"]["describe"]["episodic"]["total"] == 1
    assert "runs" in bundle


def test_replay_bundle_can_be_written(tmp_path):
    out = evaluation.replay_bundle("t4", root=tmp_path)
    assert out.get("path")
    path = tmp_path / "traces" / "t4.replay.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["task_id"] == "t4"


def test_golden_cases_roundtrip(tmp_path):
    path = tmp_path / "golden.jsonl"
    evaluation.append_golden(path, {"topic": "数码评测 3 分钟", "expect": "timeline"})
    cases = evaluation.load_golden(path)
    assert len(cases) == 1 and cases[0]["topic"].startswith("数码评测")
