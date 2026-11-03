"""降级 gate 的续跑预算按"新事实"重置（E2E 2026-10-30 实测）。"""

from __future__ import annotations

import pytest

from clipwright.services import agent_kernel
from clipwright.services import degradation_ledger


class _Task:
    id = "task_deg_budget"

    class usage:
        tool_calls = 0
        cost_usd = 0.0
        unknown_cost_calls = 0


@pytest.fixture(autouse=True)
def _clean():
    degradation_ledger.clear("task_deg_budget")
    agent_kernel._DEGRADATION_CONTINUATIONS.pop("task_deg_budget", None)
    agent_kernel._PENDING_DEGRADATIONS.pop("task_deg_budget", None)
    yield
    degradation_ledger.clear("task_deg_budget")
    agent_kernel._DEGRADATION_CONTINUATIONS.pop("task_deg_budget", None)
    agent_kernel._PENDING_DEGRADATIONS.pop("task_deg_budget", None)


def _payload(kind: str) -> dict:
    return {"status": "ok", "degraded": True,
            "output": {"degradations": [{"kind": kind, "scope": "animation"}]}}


def test_new_degradations_reset_continuation_budget():
    task = _Task()
    agent_kernel._DEGRADATION_CONTINUATIONS[task.id] = 1  # 前期已用掉续跑预算
    agent_kernel._collect_degradations(task, "delegate_agent", _payload("text_motion_weak"))
    assert agent_kernel._DEGRADATION_CONTINUATIONS.get(task.id) == 0, (
        "新降级应当重新给一次决策机会，否则后期的降级永远只能判失败")


def test_duplicate_degradation_does_not_reset_budget():
    task = _Task()
    agent_kernel._collect_degradations(task, "delegate_agent", _payload("text_motion_weak"))
    agent_kernel._DEGRADATION_CONTINUATIONS[task.id] = 1
    agent_kernel._collect_degradations(task, "delegate_agent", _payload("text_motion_weak"))
    assert agent_kernel._DEGRADATION_CONTINUATIONS.get(task.id) == 1
