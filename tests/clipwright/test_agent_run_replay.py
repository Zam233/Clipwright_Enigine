"""降级修复的幂等语义（E2E 2026-10-30 实测缺口）。

现场：主 agent 重派同一阶段"成功"，但产物 sha256 与降级那次完全相同——幂等缓存把调用
回放成历史结果，弱动效根本没改。这组测试锁三件事：
1. 上次结果有降级时**不回放**，真的重跑；
2. 干净结果回放时打 `replayed=True`；
3. 回放命中的重派**不写 remediated 收据**（不能给假收据）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services import agent_run
from clipwright.services.agent_run import AgentResult, RunSpec


class _Task:
    id = "task_replay_probe"

    class request:
        extra: dict = {}


@pytest.fixture(autouse=True)
def _clean():
    agent_run.reset()
    yield
    agent_run.reset()


def _result(status="ok", degradations=None, run_id="run_1") -> AgentResult:
    return AgentResult(run_id=run_id, kind="stage", status=status,
                       stage="animation", degradations=list(degradations or []))


def test_degraded_result_is_not_replayed(monkeypatch):
    """上次有降级 → 重派必须真的重跑（否则修复永远修不动）。"""
    calls = {"n": 0}

    async def _dispatch(spec, task, run_id, trace_id):
        calls["n"] += 1
        return _result(run_id=run_id)

    monkeypatch.setattr(agent_run, "_dispatch", _dispatch)
    agent_run._IDEM["animation:1"] = _result("degraded", [{"kind": "text_motion_weak"}])

    spec = RunSpec(kind="stage", stage="animation", idem_key="animation:1")
    first = asyncio.run(agent_run.run(spec, _Task()))
    assert calls["n"] == 1, "有降级的结果不该走回放"
    assert first.replayed is False


def test_clean_result_is_replayed_and_marked(monkeypatch):
    calls = {"n": 0}

    async def _dispatch(spec, task, run_id, trace_id):  # noqa: ANN001, ANN003
        calls["n"] += 1
        return _result(run_id=run_id)

    monkeypatch.setattr(agent_run, "_dispatch", _dispatch)
    agent_run._IDEM["animation:2"] = _result("ok")

    spec = RunSpec(kind="stage", stage="animation", idem_key="animation:2")
    out = asyncio.run(agent_run.run(spec, _Task()))
    assert calls["n"] == 0, "干净结果应当回放，不重复干活"
    assert out.replayed is True


def test_refresh_gets_new_execution_id_and_attempt(monkeypatch):
    """重放是新的执行：run_id 必须变化，attempt 必须递增。"""
    seen: dict = {}

    async def _dispatch(spec, task, run_id, trace_id):
        seen["run_id"] = run_id
        return _result(run_id=run_id)

    monkeypatch.setattr(agent_run, "_dispatch", _dispatch)
    previous = _result(run_id="animation:9")
    previous.task_id = _Task.id
    agent_run._RUNS["animation:9"] = previous

    spec = RunSpec(kind="stage", stage="animation", idem_key="animation:9", refresh=True)
    out = asyncio.run(agent_run.run(spec, _Task()))
    assert seen["run_id"].startswith("animation:9#r")
    assert out.run_id == seen["run_id"]
    assert out.attempt == 2, "同一任务同一阶段的第二次执行应记为 attempt=2"


def test_agent_run_end_carries_full_contract(monkeypatch):
    """端事件必须带完整 failure/needs/duration_ms，不能只发一个分类字符串。"""
    from clipwright.services import task_events

    task_events.clear(_Task.id, drop_spill=False)

    async def _dispatch(spec, task, run_id, trace_id):
        result = _result(run_id=run_id)
        result.duration_ms = 1234
        result.failure = {"category": "input_insufficient",
                          "retryable": "needs_upstream",
                          "blocking_point": "缺时间线", "evidence": []}
        result.needs = [{"field": "timeline", "from_stage": "edit"}]
        return result

    monkeypatch.setattr(agent_run, "_dispatch", _dispatch)
    spec = RunSpec(kind="stage", stage="animation", idem_key="animation:contract")
    asyncio.run(agent_run.run(spec, _Task()))

    end = [e for e in task_events.events(_Task.id)
           if e.type == "agent_run_end"][-1]
    assert end.detail["failure"]["category"] == "input_insufficient"
    assert end.detail["needs"][0]["field"] == "timeline"
    assert end.detail["duration_ms"] == 1234


def test_surface_reports_replayed_flag(monkeypatch):
    """工具结果里必须有 replayed，主 agent 才分得清"重跑"与"旧结果"。"""
    from clipwright.tool import surface

    async def _fake_run(spec, task):  # noqa: ANN001
        r = _result()
        r.replayed = True
        return r

    monkeypatch.setattr("clipwright.services.agent_run.run", _fake_run)
    out = asyncio.run(surface.execute(
        "delegate_agent", {"stage": "animation", "brief": "x"},
        pipeline_id="task_x", task=_Task()))
    assert out["replayed"] is True
    assert out["status"] == "ok"
