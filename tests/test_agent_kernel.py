"""C2：Agent Kernel（plan → act → observe → reflect + 四道闸 + 检查点 + 事件流）。

对应 PRD：A1（触闸优雅收尾）、A2（可复现/回放）、A3（每次调用有审计）、
A6（续跑不重复副作用）、A11（上下文不炸且关键数值不丢）。

测试全部用注入的 fake planner/executor，**不联网、不跑 ffmpeg**。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from clipwright.schema.task import Plan, PlanStep, TaskLimits, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import task_events as te


class FakePlanner:
    def __init__(self, steps: list[PlanStep] | None = None,
                 reflect_actions: list[dict] | None = None) -> None:
        self.steps = steps or [
            PlanStep(id="s1", title="探测素材", capability="media_probe",
                     args={"input_path": "a.mp4"}),
            PlanStep(id="s2", title="再探一次", capability="media_probe",
                     args={"input_path": "b.mp4"}),
        ]
        self.actions = list(reflect_actions or [])
        self.plan_calls = 0

    async def plan(self, task):  # noqa: ANN001, ANN201
        self.plan_calls += 1
        return Plan(steps=[s.model_copy(deep=True) for s in self.steps])

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        if self.actions:
            return self.actions.pop(0)
        return {"action": "continue"}


def _kernel(tmp_path, planner=None, executor=None) -> ak.AgentKernel:
    return ak.AgentKernel(ak.KernelDeps(
        planner=planner or FakePlanner(),
        executor=executor or _ok_executor,
        checkpoint_dir=tmp_path / "tasks",
    ))


async def _ok_executor(task, step):  # noqa: ANN001, ANN201
    return {"status": "ok", "output": {"duration_sec": 12.5, "frames": 300, "path": "a.mp4"}}


@pytest.fixture(autouse=True)
def _clean():
    ak.reset_kernel_state()
    te.clear()
    yield
    ak.reset_kernel_state()
    te.clear()


# ── 基本循环与事件流 ────────────────────────────────


def test_plan_then_run_emits_full_event_chain(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="做一个 30 秒视频", plan_only=False))
    task = asyncio.run(k.run(task))

    types = [e.type for e in te.events(task.id)]
    for expected in ("task_start", "plan", "step_start", "tool_call", "tool_result",
                     "step_end", "checkpoint", "task_end"):
        assert expected in types, f"缺事件 {expected}: {types}"
    assert types[-2:] == ["task_end", "checkpoint"] or "task_end" in types[-3:]
    assert task.status == "done"
    assert task.finish and task.finish.completed


def test_plan_only_does_not_execute(tmp_path):
    """Q3：默认 plan_only，规划完就停，不许偷偷开跑。"""
    planner = FakePlanner()
    called: list[str] = []

    async def ex(task, step):  # noqa: ANN001, ANN201
        called.append(step.id)
        return {"status": "ok"}

    k = _kernel(tmp_path, planner=planner, executor=ex)
    task = k.create(TaskRequest(topic="先看计划", plan_only=True))
    plan = asyncio.run(k.plan(task))
    assert len(plan.steps) == 2
    assert called == [], "plan_only 阶段不允许执行"
    assert task.status == "pending"


def test_events_have_monotonic_seq_and_bad_type_rejected(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    asyncio.run(k.run(task))
    seqs = [e.seq for e in te.events(task.id)]
    assert seqs == sorted(seqs) and seqs[0] == 1
    with pytest.raises(te.EventError, match="未知事件类型"):
        te.emit(task.id, "nonsense", "x")


def test_digest_keeps_structured_numbers(tmp_path):
    """A11：观察摘要里关键数值必须还在（不能只剩自然语言）。"""
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))
    facts = task.context_digest[0]["facts"]
    assert facts["duration_sec"] == 12.5
    assert facts["frames"] == 300


# ── 四道闸（A1）─────────────────────────────────────


def _long_planner(n: int) -> FakePlanner:
    return FakePlanner(steps=[
        PlanStep(id=f"s{i}", capability="media_probe", args={"input_path": f"{i}.mp4"})
        for i in range(1, n + 1)
    ])


def test_turn_gate_finishes_gracefully(tmp_path):
    k = _kernel(tmp_path, planner=_long_planner(10))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits = TaskLimits(max_turns=2, max_tool_calls=80, max_wall_sec=3600)
    task = asyncio.run(k.run(task))
    assert task.status == "failed"
    assert task.finish and "回合数" in task.finish.reason
    # 三件套齐全（A1）
    assert task.finish.completed and task.finish.unfinished
    assert any(e.type == "gate_hit" for e in te.events(task.id))


def test_tool_call_gate(tmp_path):
    k = _kernel(tmp_path, planner=_long_planner(10))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits = TaskLimits(max_turns=30, max_tool_calls=1, max_wall_sec=3600)
    task = asyncio.run(k.run(task))
    assert task.finish and "工具调用数" in task.finish.reason


def test_wall_gate(tmp_path, monkeypatch):
    """墙钟闸：用一个每次 +1s 的假表，避免依赖真实耗时（快机器上不稳）。

    注意：假表每调用一次就 +1s，所以触闸可能发生在第一步之前——那种情况下
    `completed` 允许为空（还没干完任何一步），但结论与未完成清单必须齐全。
    """
    ticks = {"t": 0.0}

    def fake_monotonic() -> float:
        ticks["t"] += 1.0
        return ticks["t"]

    monkeypatch.setattr(ak.time, "monotonic", fake_monotonic)
    k = _kernel(tmp_path, planner=_long_planner(5))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits = TaskLimits(max_turns=30, max_tool_calls=80, max_wall_sec=2)
    task = asyncio.run(k.run(task))
    assert task.finish and "墙钟" in task.finish.reason
    assert task.usage.wall_sec >= 2
    # 收尾三件套：结论必须写清、未完成清单不能空（还有 5 步没做）
    assert task.finish.conclusion
    assert task.finish.unfinished


def test_budget_gate_only_when_configured(tmp_path):
    """金额闸未配置=无上限（Q1）：不配就跑满计划。"""
    k = _kernel(tmp_path, planner=_long_planner(2))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits = TaskLimits(max_turns=30, max_tool_calls=80, max_wall_sec=3600,
                             budget_usd=None)
    task = asyncio.run(k.run(task))
    assert task.status == "done"
    assert task.finish.reason == "plan_completed"


def test_limits_cannot_be_empty():
    for bad in (
        TaskLimits(max_turns=0), TaskLimits(max_tool_calls=0),
        TaskLimits(max_wall_sec=0), TaskLimits(budget_usd=0),
    ):
        with pytest.raises(ValueError):
            bad.validate_or_raise()
    TaskLimits().validate_or_raise()  # 默认值合法


# ── 失败、暂停、取消、注入 ────────────────────────────


def test_step_failure_finishes_with_reason(tmp_path):
    async def fail_exec(task, step):  # noqa: ANN001, ANN201
        return {"status": "error", "error": "ffmpeg 失败"}

    k = _kernel(tmp_path, executor=fail_exec)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "failed"
    assert "step_failed:media_probe" in task.finish.reason
    assert "ffmpeg 失败" in task.finish.unfinished[0]


def test_executor_exception_is_contained(tmp_path):
    async def boom(task, step):  # noqa: ANN001, ANN201
        raise RuntimeError("执行器炸了")

    k = _kernel(tmp_path, executor=boom)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "failed"
    assert "执行器炸了" in (task.finish.unfinished[0])


def test_pause_stops_before_next_step(tmp_path):
    planner = _long_planner(5)

    async def pause_then_ok(task, step):  # noqa: ANN001, ANN201
        if step.id == "s1":
            k.pause(task.id)
        return {"status": "ok"}

    k = _kernel(tmp_path, planner=planner, executor=pause_then_ok)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "paused"
    done = [s.id for s in task.plan.steps if s.status == "done"]
    assert done == ["s1"], "暂停后不应继续执行下一步"


def test_cancel_marks_task_cancelled(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    k.cancel(task.id)
    assert k.get(task.id).status == "cancelled"


def test_inject_records_event(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    k.inject(task.id, "第 3 段换成实拍")
    assert any(e.type == "inject" for e in te.events(task.id))
    with pytest.raises(ak.KernelError):
        k.inject(task.id, "   ")


# ── 续跑与幂等（A6）─────────────────────────────────


def test_resume_from_checkpoint_skips_done_steps(tmp_path):
    """A6：续跑不重跑已完成的 Step（幂等键 + 检查点）。"""
    executed: list[str] = []

    async def count_exec(task, step):  # noqa: ANN001, ANN201
        executed.append(step.id)
        if step.id == "s1":
            k.pause(task.id)
        return {"status": "ok"}

    k = _kernel(tmp_path, planner=_long_planner(3), executor=count_exec)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert executed == ["s1"]
    assert task.status == "paused"

    # 模拟进程重启：清内存，从检查点恢复
    ak.reset_kernel_state()
    k2 = _kernel(tmp_path, planner=_long_planner(3), executor=count_exec)
    resumed = asyncio.run(k2.resume(task.id))
    assert executed == ["s1", "s2", "s3"], f"续跑不该重跑 s1：{executed}"
    assert resumed.status == "done"


def test_checkpoint_file_is_valid_json(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))
    path = tmp_path / "tasks" / f"{task.id}.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["id"] == task.id
    assert payload["status"] == "done"


def test_replan_appends_steps(tmp_path):
    planner = FakePlanner(steps=[PlanStep(id="s1", capability="media_probe",
                                          args={"input_path": "a.mp4"})],
                          reflect_actions=[
                              {"action": "replan", "why": "素材缺失，换路子",
                               "steps": [{"capability": "text_diagram", "args": {}, "title": "补图"}]},
                              {"action": "continue"},
                          ])
    k = _kernel(tmp_path, planner=planner)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert len(task.plan.steps) == 2
    assert task.plan.version == 2
    assert any(e.type == "replan" for e in te.events(task.id))


def test_reflect_finish_ends_task(tmp_path):
    planner = FakePlanner(steps=[PlanStep(id="s1", capability="media_probe")],
                          reflect_actions=[{"action": "finish", "conclusion": "够了"}])
    k = _kernel(tmp_path, planner=planner)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "failed"      # 非 plan_completed 收尾
    assert task.finish.reason == "够了"
