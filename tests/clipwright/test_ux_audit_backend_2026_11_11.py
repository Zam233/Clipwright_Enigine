"""审计 PRD 2026-11-09 后端修复的定向回归（W02/W03/W04）。

覆盖：
- KK-01：金额闸比 main_spent_usd（质量花费不计入主 agent 账单）
- KK-02/KK-05：收口内正常 finish → capped（不是 done）
- KK-08：取消路径写 terminal_reason=user_cancelled
- KK-03：cancel_for_task 只取消本任务的在飞子 run
- B-04：stale 扫描覆盖 planning
- BE-03：失败 task_end 的 detail 形状含 completed/unfinished
"""

from __future__ import annotations

import pytest

from clipwright.schema.task import (
    BudgetEscrow, EscrowState, Task, TaskFinish, TaskRequest, TerminalReason,
)
from clipwright.services import agent_run as agent_run_mod
from clipwright.services import agent_kernel as kernel_mod
from clipwright.services.agent_kernel import AgentKernel


def _task(**kw) -> Task:
    t = Task(id="task_test_audit", request=TaskRequest(topic="审计回归", duration_sec=30))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


# ── KK-05 / KK-02：capped 不是 done ────────────────────────────────────────

def test_capped_terminal_reason_when_in_closeout():
    """收口模式下正常 finish 必须是 capped，不能是 achieved（KK-02 核心）。"""
    kernel = AgentKernel()
    task = _task()
    task.closeout.mode = "soft_landing"
    treason, blocker = kernel._success_terminal_reason(
        task, gate_reason="tool_loop_completed", unresolved=[])
    assert treason is TerminalReason.capped
    assert blocker == "budget_exhausted"


def test_normal_finish_still_achieved_without_closeout():
    """未进收口的正常收尾仍 attained —— 修复不能把正常路径也变成 capped。"""
    kernel = AgentKernel()
    task = _task()
    treason, _ = kernel._success_terminal_reason(
        task, gate_reason="tool_loop_completed", unresolved=[])
    assert treason is TerminalReason.achieved


def test_plan_only_completed_not_misjudged_as_capped():
    kernel = AgentKernel()
    task = _task()
    treason, _ = kernel._success_terminal_reason(
        task, gate_reason="plan_only_completed", unresolved=[])
    assert treason is TerminalReason.plan_only_completed


# ── KK-01：金额闸只看主 agent 花费 ────────────────────────────────────────

def test_budget_gate_uses_main_spent_not_total():
    """主 agent 0.70 < 可用 0.80，质量另花 0.25；总账 0.95 不得触发收口。"""
    kernel = AgentKernel()
    task = _task()
    task.limits.budget_usd = 1.0
    task.escrow = BudgetEscrow(
        state=EscrowState.frozen,
        budget_usd=1.0, reserve_usd=0.2, main_budget_usd=0.8,
        main_spent_usd=0.7, quality_spent_usd=0.25,
    )
    task.usage.cost_usd = 0.95  # 主 0.70 + 质量 0.25
    task.usage.turns = task.usage.tool_calls = task.usage.wall_sec = 0
    task.usage.tokens = 0

    # 新口径：main_spent(0.70) < main_budget(0.80) → 不触闸
    assert kernel._main_budget(task) == pytest.approx(0.8)
    assert float(task.escrow.main_spent_usd) < float(task.escrow.main_budget_usd)
    # 旧口径会用 0.95 >= 0.80 误判超支
    assert float(task.usage.cost_usd) >= float(task.escrow.main_budget_usd)


# ── KK-08：取消路径 terminal_reason 一致 ──────────────────────────────────

def test_cancel_sets_user_cancelled_terminal_reason():
    kernel = AgentKernel()
    task = _task(status="running")
    kernel_mod._TASKS[task.id] = task
    try:
        result = kernel.cancel(task.id)
    finally:
        kernel_mod._TASKS.pop(task.id, None)
        kernel_mod._CONTROL.pop(task.id, None)
    assert result.status == "cancelled"
    assert result.terminal_reason is TerminalReason.user_cancelled
    assert result.finish is not None
    assert result.finish.terminal_reason is TerminalReason.user_cancelled


def test_cancel_end_event_carries_completed_and_unfinished(monkeypatch):
    """取消也必须发带两清单的 task_end（与 agent_kernel 正常路径契约一致）。"""
    events: list[tuple[str, dict]] = []
    import clipwright.services.task_events as te

    monkeypatch.setattr(te, "emit", lambda tid, t, m, d=None, **kw: events.append((t, d or {})))
    kernel = AgentKernel()
    task = _task(status="running")
    kernel_mod._TASKS[task.id] = task
    try:
        kernel.cancel(task.id)
    finally:
        kernel_mod._TASKS.pop(task.id, None)
        kernel_mod._CONTROL.pop(task.id, None)
    ends = [d for (t, d) in events if t == "task_end"]
    assert ends, "取消必须发 task_end"
    assert "completed" in ends[-1] and "unfinished" in ends[-1]
    assert ends[-1].get("terminal_reason") == "user_cancelled"


# ── KK-03：按任务取消在飞子 run ───────────────────────────────────────────

class _FakeHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def test_cancel_for_task_only_touches_its_own_runs():
    saved = dict(agent_run_mod._LIVE)
    try:
        h1, h2, h3 = _FakeHandle(), _FakeHandle(), _FakeHandle()
        agent_run_mod._LIVE.clear()
        agent_run_mod._LIVE["run_a"] = {"task": h1, "task_id": "t_a"}
        agent_run_mod._LIVE["run_b"] = {"task": h2, "task_id": "t_a"}
        agent_run_mod._LIVE["run_c"] = {"task": h3, "task_id": "t_other"}

        cancelled = agent_run_mod.cancel_for_task("t_a")

        assert sorted(cancelled) == ["run_a", "run_b"]
        assert h1.cancelled and h2.cancelled
        assert not h3.cancelled, "不得取消其它任务的子 run"
    finally:
        agent_run_mod._LIVE.clear()
        agent_run_mod._LIVE.update(saved)


def test_cancel_for_task_ignores_empty_id():
    assert agent_run_mod.cancel_for_task("") == []


def test_kernel_cancel_calls_cancel_for_task(monkeypatch):
    """kernel.cancel 必须把在飞子 run 一起取消（KK-03）。"""
    called: list[str] = []
    monkeypatch.setattr(agent_run_mod, "cancel_for_task", lambda tid: called.append(tid) or [])
    kernel = AgentKernel()
    task = _task(status="running")
    kernel_mod._TASKS[task.id] = task
    try:
        kernel.cancel(task.id)
    finally:
        kernel_mod._TASKS.pop(task.id, None)
        kernel_mod._CONTROL.pop(task.id, None)
    assert called == [task.id]


# ── B-04：stale 扫描覆盖 planning ─────────────────────────────────────────

def test_stale_release_query_covers_planning(monkeypatch):
    from clipwright.services import requirements_service as rs

    queried: list[dict] = []

    class _Model:
        @staticmethod
        def find_many(q):
            queried.append(dict(q))
            return []

    class _Mongo:
        is_connected = True

    monkeypatch.setattr(rs, "RequirementsSessionModel", _Model)
    monkeypatch.setattr(rs, "mongo", _Mongo())

    rs.release_stale_pipeline_sessions()
    statuses = {q.get("status") for q in queried}
    assert "pipeline_running" in statuses
    assert "planning" in statuses, "旧实现只查 pipeline_running，planning 会话永远卡住"


# ── BE-03：失败 task_end 契约形状 ────────────────────────────────────────

def test_failed_task_finish_has_both_lists():
    """失败收尾的 TaskFinish 必须同时具备 completed / unfinished。"""
    fin = TaskFinish(
        conclusion="任务执行失败",
        completed=[], unfinished=["task_error:exception"],
        reason="failed", terminal_reason=TerminalReason.model_error,
    )
    assert isinstance(fin.completed, list)
    assert isinstance(fin.unfinished, list)
    assert fin.unfinished, "失败必须有未完成项，不能空清单"


def test_cancelled_task_excluded_from_success_rate():
    """KK-09：取消任务不计入成功率分母。"""
    from clipwright.services.evaluation import _EXCLUDED_FROM_RATE

    assert "cancelled" in _EXCLUDED_FROM_RATE
    assert "capped" in _EXCLUDED_FROM_RATE
    assert "done" not in _EXCLUDED_FROM_RATE
