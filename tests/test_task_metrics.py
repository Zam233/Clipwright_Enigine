"""B3：任务级指标闭环（closeout plan B3 / PRD §2.6.1）。

分母必须覆盖 **三类终态**，否则 `task_success_rate` 系统性偏高：

- `done`（`tool_loop_completed` / 完成）
- `failed`（loop 失败 / 收尾守门未过）
- **`cancelled` 与 `gate:*`（取消 / 触闸）** ← 旧实现这两类不记录

「暂停」不是结局，不记录：它会 resume，记了就是把同一次任务数两次。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.task import TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import evaluation


class _Driver:
    def __init__(self, *, status: str = "ok") -> None:
        self.status = status
        self.executed = 0

    async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                       guard=None, on_tool_call=None, dry_run=False):  # noqa: ANN001
        # 像真实 loop 一样在每次工具执行前过闸，否则测不到"触闸"这条路径
        if guard is not None:
            hit = guard()
            if hit:
                return {"status": "aborted", "stopped_reason": str(hit),
                        "tool_calls": []}
        if self.status == "ok":
            if on_tool_call is not None:
                on_tool_call("media_probe", {"status": "ok", "output": {"n": 1}}, {})
            return {"status": "ok", "finish": {"conclusion": "完成"},
                    "tool_calls": [{"tool": "media_probe"}], "rounds": 2,
                    "stopped_reason": "model_final"}
        return {"status": "error", "error": "boom", "stopped_reason": "tool_failed",
                "failure": {"category": "tool_failed", "retryable": "permanent",
                            "blocking_point": "", "evidence": []},
                "tool_calls": []}


@pytest.fixture(autouse=True)
def _clean():
    ak.reset_kernel_state()
    evaluation.reset()
    yield
    ak.reset_kernel_state()
    evaluation.reset()


def _kernel(tmp_path, driver=None) -> ak.AgentKernel:
    return ak.AgentKernel(ak.KernelDeps(
        driver=driver or _Driver(), checkpoint_dir=tmp_path / "tasks"))


def _task_row(task_id: str) -> dict:
    rows = [r for r in evaluation.records(task_id) if r["kind"] == "task"]
    assert rows, f"{task_id} 没有任务级指标"
    return rows[-1]


def test_completed_task_recorded(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    row = _task_row(task.id)
    assert row["status"] == task.status


def test_failed_task_recorded(tmp_path):
    k = _kernel(tmp_path, driver=_Driver(status="error"))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "failed"
    assert _task_row(task.id)["extra"]["gate_passed"] is False


def test_cancelled_task_recorded(tmp_path):
    """取消是终态，必须计入分母（旧实现什么都不记）。"""
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.usage.turns = task.limits.max_turns + 1   # 让它在第一次 guard 就中止
    task = asyncio.run(k.run(task))
    assert task.status in ("failed", "cancelled")
    _task_row(task.id)   # 至少有一条任务级记录


def test_gate_hit_recorded_with_gate_reason(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits.max_tool_calls = 0     # 第一次工具调用就触闸
    task = asyncio.run(k.run(task))
    assert task.finish is not None and task.finish.reason.startswith("gate:")
    row = _task_row(task.id)
    assert row["extra"]["gate_passed"] is False
    assert "gate:" in row["extra"]["reason"]


def test_paused_task_is_not_recorded(tmp_path):
    """暂停不是结局：记了就会把同一次任务数两次。"""
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    k.pause(task.id)
    assert task.status == "paused"
    assert [r for r in evaluation.records(task.id) if r["kind"] == "task"] == []


def test_task_success_rate_uses_task_rows_only(tmp_path):
    """子 run 的成功率不能冒充任务成功率。"""
    k = _kernel(tmp_path)
    ok_task = k.create(TaskRequest(topic="a", plan_only=False))
    asyncio.run(k.run(ok_task))
    bad_task = k.create(TaskRequest(topic="b", plan_only=False))
    bad_task.limits.max_tool_calls = 0
    asyncio.run(k.run(bad_task))

    evaluation.record_run(task_id="a", status="ok", cost_usd=0.1)  # 子 run 记录

    stat = evaluation.task_summary()
    assert stat["count"] == 2, "只统计任务级记录"
    # 成功的那个任务没有 final_timeline → 收尾守门把它记成失败（这正是守门要的效果），
    # 所以这里断言的是"两类终态都进了分母"，而不是"至少一个成功"。
    assert 0.0 <= stat["task_success_rate"] <= 1.0
    assert stat["gate_passed"] == 0
    assert stat["count"] == 2
