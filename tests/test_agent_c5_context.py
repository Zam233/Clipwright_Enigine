"""C5：主 agent 的上下文与插话（M3 后的形态）。

M3 删掉了计划表执行，所以这里改成**驱动器**注入：脚本化驱动器复现"模型调了哪些工具"。
覆盖的不变量不变：插话必须能进上下文并被消费、上下文压缩要有界且关键数值不丢、
子代理能力的安全边界不许放宽。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from clipwright.config import settings
from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import agent_run as arun
from clipwright.services import task_events as te


class ScriptedDriver:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.calls = calls or [("media_probe", {"input_path": "a.mp4"})]
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, task, *, context_text="", shadow=False, dry_run=False,  # noqa: ANN001
                       guard=None, on_tool_call=None) -> dict[str, Any]:
        for name, args in self.calls:
            result = {"status": "ok",
                      "output": {"duration_sec": 12.5, "frames": 300}}
            self.executed.append((name, args))
            if on_tool_call is not None:
                on_tool_call(name, result)
        return {"status": "ok", "finish": {"conclusion": "ok"},
                "tool_calls": [{"tool": c[0]} for c in self.executed],
                "rounds": len(self.executed) + 1, "stopped_reason": "model_final"}


class RecordingPlanner:
    """替代旧的 fake planner：只记录"这一版假设看到了哪些插话"。"""

    def __init__(self) -> None:
        self.seen_hints: list[list[str]] = []

    async def __call__(self, self_planner, task):  # noqa: ANN001, ANN002
        self.seen_hints.append(list(task.hints))
        return Plan(steps=[PlanStep(id="p1", capability="media_probe")])


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ak.reset_kernel_state()
    te.clear()
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    yield
    ak.reset_kernel_state()
    te.clear()


def _kernel(tmp_path, driver=None) -> ak.AgentKernel:
    return ak.AgentKernel(ak.KernelDeps(
        driver=driver or ScriptedDriver(),
        checkpoint_dir=tmp_path / "tasks",
    ))


# ── 插话进上下文 ────────────────────────────────


def test_inject_reaches_hypothesis_and_is_consumed(tmp_path, monkeypatch):
    recorder = RecordingPlanner()
    seen: list[list[str]] = []

    async def fake_plan(self, task):  # noqa: ANN001, ARG001
        seen.append(list(task.hints))
        return Plan(steps=[PlanStep(id="p1", capability="media_probe")])

    monkeypatch.setattr(ak.LLMPlanner, "plan", fake_plan)
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="县域经济"))

    k.inject(task.id, "第 3 段换成实拍")
    assert task.hints == ["第 3 段换成实拍"]

    asyncio.run(k.plan(task))
    assert seen[0] == ["第 3 段换成实拍"], "插话必须进上下文"
    # 消费后清空：否则会一直挂着，让用户以为没被听到
    assert task.hints == []
    assert recorder.seen_hints == []


def test_inject_persists_through_checkpoint(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    k.inject(task.id, "别用配音，改字幕")

    ak.reset_kernel_state()
    reloaded = _kernel(tmp_path).get(task.id)
    assert reloaded is not None
    assert reloaded.hints == ["别用配音，改字幕"], "插话要能跨重启存活"


def test_inject_event_counts_pending(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    k.inject(task.id, "a")
    k.inject(task.id, "b")
    inject_events = [e for e in te.events(task.id) if e.type == "inject"]
    assert len(inject_events) == 2
    assert inject_events[-1].detail["pending_hints"] == 2


# ── 上下文压缩（Q5）───────────────────────────────


def test_context_bundle_keeps_numbers(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))
    bundle = ak.context_bundle(task)
    assert "duration_sec=12.5" in bundle
    assert "frames=300" in bundle


def test_context_bundle_is_bounded_for_long_tasks(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    task.context_digest = [
        {"step_id": f"s{i}", "capability": "media_probe", "status": "done",
         "brief": "x" * 200, "facts": {"duration_sec": i, "path": f"a{i}.mp4"}}
        for i in range(200)
    ]
    bundle = ak.context_bundle(task, max_chars=1200)
    assert len(bundle) <= 1200, "上下文必须有上界，否则长任务会因超限失败"
    assert "duration_sec=199" in bundle, "但最近的数值仍要在"


def test_digest_is_bounded(tmp_path):
    """跑满 30 次工具调用后 digest 会被截断，但最近的数值仍在。"""
    calls = [("media_probe", {"input_path": f"{i}.mp4"}) for i in range(30)]
    k = _kernel(tmp_path, driver=ScriptedDriver(calls))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    assert len(task.context_digest) <= ak._MAX_DIGEST + 1, "工作记忆必须有界"
    last = task.context_digest[-1]
    assert last["facts"].get("frames") == 300


# ── 子代理能力（A12）───────────────────────────────


def _kernel_result():
    """一个最小 Task（供能力单测用，不走 Kernel 主循环）。"""
    from clipwright.schema.task import Task

    return Task(id="task_c5", request=TaskRequest(topic="县域经济"))


def _research(**kw):
    from clipwright.schema.run import RunSpec

    return asyncio.run(arun.run(RunSpec(kind="research", depth=1, **kw), _kernel_result()))


def test_research_refuses_exec_tools():
    """最重要的一条：运行时不能自己给子 agent 开 shell 权限。"""
    out = _research(role="运维", brief="查一下", tools=["run_shell"])
    assert out.status == "refused"
    assert "执行权限" in out.error
    assert "审核" in out.error or "两级" in out.error


def test_research_requires_readonly_subset():
    out = _research(role="核查员", brief="查一下", tools=["write_file"])
    assert out.status == "refused"
    assert "只读子集" in out.error


def test_research_spawns_readonly_worker(monkeypatch):
    from clipwright.supervisor import spawn as sp

    captured: dict = {}

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001
        captured.update(kwargs, pid=ctx.pipeline_id)
        return {"subagent_id": "sa_1", "status": "done",
                "output": "第 3 段是占位图", "cost_usd": 0.02, "duration_ms": 1200}

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    out = _research(role="素材可用性核查员", brief="查占位图",
                    tools=["read_file", "list_dir"])
    assert out.status == "ok"
    assert out.outputs["subagent_id"] == "sa_1"
    assert out.cost_usd == 0.02
    assert set(captured["tools"]) == {"list_dir", "read_file"}
    assert not set(captured["tools"]) & arun.EXEC_TOOLS


def test_spawn_capability_no_longer_routed_by_executor(monkeypatch, tmp_path):
    """M3：能力名别名表随执行器一起删除了——旧名不再被"翻译"后执行。

    旧检查点会被迁移成假设注记（见下一条用例），不会再有人替它执行。
    """
    assert not hasattr(ak, "_TOOL_ALIAS")
    assert not hasattr(ak, "default_executor")


def test_legacy_checkpoint_steps_become_hypothesis_only(tmp_path):
    """旧检查点（计划表时代）读回来必须标成"仅假设"，不能假装还有进度。"""
    import json

    from clipwright.schema.task import Task

    k = _kernel(tmp_path)
    task = Task(id="task_old", request=TaskRequest(topic="x"),
                plan=Plan(steps=[
                    PlanStep(id="p1", capability="run_stage", args={"stage": "edit"}),
                    PlanStep(id="p2", capability="delegate"),
                ]))
    path = tmp_path / "tasks" / "task_old.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task.model_dump(mode="json"), ensure_ascii=False),
                    encoding="utf-8")

    loaded = k.get("task_old")
    assert loaded is not None
    assert loaded.plan is not None and loaded.plan.hypothesis_only is True
    assert all(s.status == "skipped" for s in loaded.plan.steps)
    assert any(e["step_id"] == "migration" for e in loaded.context_digest)
