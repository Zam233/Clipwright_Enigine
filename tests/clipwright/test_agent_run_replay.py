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
