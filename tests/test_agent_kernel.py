"""Agent Kernel（M3 后的形态）：**没有计划表执行**，只有 tool loop + 四道闸 + 检查点 + 事件流。

对应 PRD：A1（触闸优雅收尾）、A2（可复现/回放）、A3（每次调用有审计）、
A6（续跑不重复副作用）、A11（上下文不炸且关键数值不丢）。

M3 把 `_run_step`/`_after_step`/`next_step()` 物理删除了，所以这里的注入点变成
**驱动器**（`KernelDeps.driver`）：测试用脚本化驱动器复现"模型调了哪些工具"，
不再用"假规划器 + 假执行器"这套计划表玩具。**不联网、不跑 ffmpeg。**
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import task_events as te


# ── 脚本化驱动器（替代旧的 FakePlanner/FakeExecutor）──────────


class ScriptedDriver:
    """按脚本"调用工具"，行为等价于模型在 tool loop 里发出的那些 tool_call。"""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]] | None = None,
                 *, status: str = "ok", error: str = "", conclusion: str = "做完了",
                 rounds: int | None = None) -> None:
        self.calls = calls or [("media_probe", {"input_path": "a.mp4"})]
        self.status = status
        self.error = error
        self.conclusion = conclusion
        self.rounds = rounds
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.guard_hit: str = ""

    async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                       guard=None, on_tool_call=None) -> dict[str, Any]:
        for name, args in self.calls:
            if guard is not None:
                hit = guard()
                if hit:
                    self.guard_hit = str(hit)
                    return {"status": "aborted", "error": str(hit),
                            "stopped_reason": str(hit),
                            "tool_calls": [{"tool": c[0]} for c in self.executed]}
            result = {"status": "ok",
                      "output": {"duration_sec": 12.5, "frames": 300, "path": "a.mp4"}}
            self.executed.append((name, args))
            if on_tool_call is not None:
                on_tool_call(name, result)
        if self.status != "ok":
            return {"status": self.status, "error": self.error,
                    "stopped_reason": "driver_error", "tool_calls": []}
        return {
            "status": "ok",
            "finish": {"conclusion": self.conclusion},
            "tool_calls": [{"tool": c[0]} for c in self.executed],
            "rounds": self.rounds if self.rounds is not None else len(self.executed) + 1,
            "stopped_reason": "model_final",
        }


def _kernel(tmp_path, driver=None, *, mode: str = "tool_loop") -> ak.AgentKernel:
    return ak.AgentKernel(
        ak.KernelDeps(driver=driver or ScriptedDriver(),
                      checkpoint_dir=tmp_path / "tasks", kernel_mode=mode),
    )


@pytest.fixture(autouse=True)
def _clean():
    ak.reset_kernel_state()
    te.clear()
    yield
    ak.reset_kernel_state()
    te.clear()


# ── 基本循环与事件流 ────────────────────────────────


def test_run_emits_full_event_chain(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="做一个 30 秒视频", plan_only=False))
    task = asyncio.run(k.run(task))

    types = [e.type for e in te.events(task.id)]
    for expected in ("task_start", "context_loaded", "task_end", "checkpoint"):
        assert expected in types, f"缺事件 {expected}: {types}"
    assert types[-1] == "checkpoint" or "task_end" in types[-3:]
    assert task.status in ("done", "failed")


def test_plan_is_only_a_hypothesis_and_never_executes(tmp_path, monkeypatch):
    """Q3 + M3：`plan()` 只产出初始假设，驱动器一次都不许被调用。"""
    driver = ScriptedDriver()
    k = _kernel(tmp_path, driver=driver)

    async def fake_plan(self, task):  # noqa: ANN001, ARG001
        return Plan(steps=[PlanStep(id="p1", capability="delegate_agent",
                                    args={"stage": "structure"})])

    monkeypatch.setattr(ak.LLMPlanner, "plan", fake_plan)
    task = k.create(TaskRequest(topic="先看计划", plan_only=True))
    plan = asyncio.run(k.plan(task))
    assert len(plan.steps) == 1
    assert plan.hypothesis_only is True
    assert driver.executed == [], "plan_only 阶段不允许执行"
    assert task.status == "pending"
    plan_events = [e for e in te.events(task.id) if e.type == "plan"]
    assert plan_events and plan_events[0].detail.get("hypothesis_only") is True


def test_no_step_cursor_api_survives():
    """可证伪边界：执行游标真的没了（不是改名藏起来）。"""
    assert not hasattr(Plan, "next_step")
    assert not hasattr(ak, "_run_plan_loop")
    assert not hasattr(ak, "_run_step")
    assert not hasattr(ak, "_after_step")
    assert not hasattr(ak, "default_executor")


def test_events_have_monotonic_seq_and_bad_type_rejected(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    asyncio.run(k.run(task))
    seqs = [e.seq for e in te.events(task.id)]
    assert seqs == sorted(seqs) and seqs[0] == 1
    with pytest.raises(te.EventError, match="未知事件类型"):
        te.emit(task.id, "nonsense", "x")


def test_events_carry_trace_envelope(tmp_path):
    """P1-7：事件要能按 trace 串起来（trace/run/span 在信封上，不埋在 detail 里）。"""
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    asyncio.run(k.run(task))
    evs = te.events(task.id)
    assert all(e.trace_id == task.id for e in evs)
    # to_public 只在真有值时输出，老前端不受影响
    public = evs[0].to_public()
    assert public["trace_id"] == task.id


def test_digest_keeps_structured_numbers(tmp_path):
    """A11：关键数值必须留痕（`_observe` 删了，改由 tool loop 每次调用落一次）。"""
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))
    facts = task.context_digest[0]["facts"]
    assert facts["duration_sec"] == 12.5
    assert facts["frames"] == 300


# ── 四道闸（A1）─────────────────────────────────────


def test_tool_call_gate_aborts_mid_loop(tmp_path):
    """工具调用闸在**循环中途**生效：到顶就不再执行后续工具。"""
    driver = ScriptedDriver([("media_probe", {"i": 1}), ("media_probe", {"i": 2}),
                             ("media_probe", {"i": 3})])
    k = _kernel(tmp_path, driver=driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits.max_tool_calls = 1
    task = asyncio.run(k.run(task))
    assert len(driver.executed) == 1, "到顶后不许继续执行"
    assert driver.guard_hit.startswith("工具调用数达到上限")
    assert task.status == "paused"
    assert any(e.type == "gate_hit" for e in te.events(task.id))


def test_turn_gate_reported_by_gate_hit(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.usage.turns = task.limits.max_turns
    task.usage.wall_sec = 0.0
    assert k._gate_hit(task, 0.0).startswith("回合数达到上限")


def test_wall_gate_reported_by_gate_hit(tmp_path, monkeypatch):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits.max_wall_sec = 1.0
    monkeypatch.setattr(ak.time, "monotonic", lambda: 10_000.0)
    # started 与 monotonic 同源，才不会一上来就被墙钟闸拦住
    assert "墙钟达到上限" in str(k._gate_hit(task, 9_995.0))


def test_budget_gate_only_when_configured(tmp_path, monkeypatch):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits.max_wall_sec = 3600.0
    monkeypatch.setattr(ak.time, "monotonic", lambda: 100.0)
    task.usage.cost_usd = 99.0
    assert k._gate_hit(task, 100.0) is None, "没配预算就不该有金额闸"
    task.limits.budget_usd = 1.0
    assert "预算耗尽" in str(k._gate_hit(task, 100.0))


def test_driver_failure_finishes_with_reason(tmp_path):
    driver = ScriptedDriver(status="error", error="模型挂了")
    k = _kernel(tmp_path, driver=driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "failed"
    assert task.finish and "工具循环失败" in task.finish.conclusion
    assert task.finish.reason.startswith("tool_loop_error")


def test_pause_then_resume_reruns_loop_and_keeps_checkpoint(tmp_path):
    """暂停后可从检查点续跑；续跑就是重进 loop（没有"已完成步骤"可跳）。"""
    driver = ScriptedDriver()
    k = _kernel(tmp_path, driver=driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.usage.turns = task.limits.max_turns          # 第一次直接触闸 → paused
    task = asyncio.run(k.run(task))
    assert task.status == "paused"
    assert task.usage.turns >= 1

    task.usage.turns = 0                              # 用户点"继续"
    task = asyncio.run(k.resume(task.id))
    assert task.status in ("done", "failed")
    assert (tmp_path / "tasks" / f"{task.id}.json").exists()


def test_cancel_marks_task_cancelled(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = k.cancel(task.id)
    assert task.status == "cancelled"
    assert any(e.type == "task_end" for e in te.events(task.id))


def test_inject_records_event_and_is_consumed_by_hypothesis(tmp_path, monkeypatch):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=True))
    k.inject(task.id, "最后一镜要慢一点")
    assert any(e.type == "inject" for e in te.events(task.id))
    assert task.hints

    async def fake_plan(self, task):  # noqa: ANN001, ARG001
        return Plan(steps=[PlanStep(id="p1", capability="media_probe")])

    monkeypatch.setattr(ak.LLMPlanner, "plan", fake_plan)
    asyncio.run(k.plan(task))
    assert task.hints == [], "插话被这一版假设消费掉，不能一直挂着"


def test_checkpoint_file_is_valid_json(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))
    path = tmp_path / "tasks" / f"{task.id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["id"] == task.id
    assert (tmp_path / "tasks" / f"{task.id}.state.json").exists()


def test_shadow_mode_records_but_does_not_judge_completion(tmp_path):
    """shadow：跑 loop 但不据此判定"任务完成"（用于与真实执行对比）。"""
    driver = ScriptedDriver(conclusion="影子结论")
    k = _kernel(tmp_path, driver=driver, mode="shadow")
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.finish is not None
    # shadow 不套收尾守门：没有时间线也按 loop 结论收尾（它本来就不该产生副作用）
    assert task.finish.reason == "tool_loop_completed"


def test_tool_loop_unavailable_fails_honestly(tmp_path, monkeypatch):
    """M3：条件不满足时**如实失败**，不再偷偷换回计划表执行。"""
    from clipwright.services import agent_loop

    k = _kernel(tmp_path)
    monkeypatch.setattr(agent_loop, "tool_loop_available",
                        lambda: (False, "no_llm_credential"))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "failed"
    assert "no_llm_credential" in (task.finish.reason if task.finish else "")
    assert any(e.type == "degradation"
               and e.detail.get("kind") == "kernel_tool_loop_unavailable"
               for e in te.events(task.id))


def test_legacy_plan_mode_is_reported_not_honoured(tmp_path, monkeypatch):
    from clipwright.services import agent_loop

    k = ak.AgentKernel(ak.KernelDeps(driver=ScriptedDriver(),
                                     checkpoint_dir=tmp_path / "tasks",
                                     kernel_mode="plan"))
    assert k.kernel_mode == "tool_loop"
    monkeypatch.setattr(agent_loop, "tool_loop_available", lambda: (True, "ok"))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert any(e.type == "degradation" and e.detail.get("kind") == "kernel_mode_removed"
               for e in te.events(task.id))
