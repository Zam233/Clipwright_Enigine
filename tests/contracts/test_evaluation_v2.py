"""B11：评估口径 v2 —— 0 成本 != unknown；capped 单列且不进成功率。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.services import evaluation, golden_eval


def test_zero_cost_is_known_not_unknown():
    assert evaluation._cost_of(0) == (0.0, False)
    row = evaluation.record_run(task_id="t", run_id="r", status="ok", cost_usd=0)
    assert row["cost_usd"] == 0.0 and row["cost_unknown"] is False

    unknown = evaluation.record_run(task_id="t", run_id="r2", status="ok", cost_usd=None)
    assert unknown["cost_usd"] is None and unknown["cost_unknown"] is True


def test_task_summary_counts_capped_separately():
    evaluation.reset()
    evaluation.record_task(task_id="a", status="done", reason="task_completed",
                           gate_passed=True, cost_usd=0)
    evaluation.record_task(task_id="b", status="capped", reason="capped",
                           gate_passed=True, cost_usd=0.1)
    summary = evaluation.task_summary()
    assert summary["count"] == 1 and summary["capped_count"] == 1
    assert summary["task_success_rate"] == 1.0
    assert summary["cost_total_usd"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_run_many_excludes_capped_from_success(monkeypatch):
    canned = [
        {"task_id": "a", "status": "done", "capped": False, "turns": 2,
         "tool_calls": 3, "wall_sec": 1.0, "cost_usd": 0.0, "terminal_reason": "completed"},
        {"task_id": "b", "status": "capped", "capped": True, "turns": 9,
         "tool_calls": 9, "wall_sec": 2.0, "cost_usd": 0.2, "terminal_reason": "capped"},
        {"task_id": "c", "status": "failed", "capped": False, "turns": 1,
         "tool_calls": 0, "wall_sec": 0.5, "cost_usd": None, "terminal_reason": "internal_defect"},
    ]
    calls = {"n": 0}

    async def fake_once(topic, **kw):
        row = dict(canned[calls["n"]])
        calls["n"] += 1
        return row

    monkeypatch.setattr(golden_eval, "run_once", fake_once)
    out = await golden_eval.run_many(SimpleNamespace(id="t", topic="x"), runs=3)
    assert out["capped_count"] == 1
    assert out["report"]["eligible_runs"] == 2
    assert out["report"]["metrics"]["success_rate"]["n"] == 2
    assert out["report"]["metrics"]["success_rate"]["mean"] == 0.5
    assert any("capped" in w for w in out["report"].get("warnings") or [])


@pytest.mark.asyncio
async def test_run_many_all_capped_is_reported_not_hidden(monkeypatch):
    async def fake_once(topic, **kw):
        return {"task_id": "a", "status": "capped", "capped": True, "turns": 1,
                "tool_calls": 1, "wall_sec": 1.0, "cost_usd": 0.0,
                "terminal_reason": "capped"}

    monkeypatch.setattr(golden_eval, "run_once", fake_once)
    out = await golden_eval.run_many(SimpleNamespace(id="t", topic="x"), runs=2)
    assert out["capped_count"] == 2
    assert out["report"]["eligible_runs"] == 0
    assert any("capped" in w for w in out["report"].get("warnings") or [])
