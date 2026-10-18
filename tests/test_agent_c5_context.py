"""C5：记忆与上下文（插话生效 / 压缩不丢数值 / 子代理能力）。

三条对应 PRD：
- Q5：摘要优先，但**关键数值必须结构化保留**；
- US2/A12：agent 能派只读子代理去看现场，但**不能自己开执行权限**；
- 用户插话必须进规划上下文（否则"插了等于没插"）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.config import settings
from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import subagent_capability as sub
from clipwright.services import task_events as te


class RecordingPlanner:
    """记录每次规划时看到的 hints（用来证明插话真的进了上下文）。"""

    def __init__(self) -> None:
        self.seen_hints: list[list[str]] = []

    async def plan(self, task):  # noqa: ANN001, ANN201
        self.seen_hints.append(list(task.hints))
        return Plan(steps=[PlanStep(id="s1", capability="media_probe",
                                    args={"input_path": "a.mp4"})])

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        return {"action": "continue"}


async def ok_executor(task, step):  # noqa: ANN001, ANN201
    return {"status": "ok", "output": {"duration_sec": 12.5, "frames": 300}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ak.reset_kernel_state()
    te.clear()
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    yield
    ak.reset_kernel_state()
    te.clear()


def _kernel(tmp_path, planner=None):
    return ak.AgentKernel(ak.KernelDeps(
        planner=planner or RecordingPlanner(),
        executor=ok_executor,
        checkpoint_dir=tmp_path / "tasks",
    ))


# ── 插话进上下文 ────────────────────────────────────────


def test_inject_reaches_planner_and_is_consumed(tmp_path):
    planner = RecordingPlanner()
    k = _kernel(tmp_path, planner)
    task = k.create(TaskRequest(topic="县域经济"))

    k.inject(task.id, "第 3 段换成实拍")
    assert task.hints == ["第 3 段换成实拍"]

    asyncio.run(k.plan(task))
    assert planner.seen_hints[0] == ["第 3 段换成实拍"], "插话必须进规划上下文"
    # 消费后清空：否则会一直挂着，让用户以为没被听到
    assert task.hints == []


def test_inject_persists_through_checkpoint(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    k.inject(task.id, "别用配音，改用字幕")

    ak.reset_kernel_state()
    reloaded = _kernel(tmp_path).get(task.id)
    assert reloaded is not None
    assert reloaded.hints == ["别用配音，改用字幕"], "插话要能跨重启存活"


def test_inject_event_counts_pending(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x"))
    k.inject(task.id, "a")
    k.inject(task.id, "b")
    inject_events = [e for e in te.events(task.id) if e.type == "inject"]
    assert len(inject_events) == 2
    assert inject_events[-1].detail["pending_hints"] == 2


# ── 上下文压缩（Q5）───────────────────────────────────


def test_context_bundle_keeps_numbers_and_skips_pending(tmp_path):
    k = _kernel(tmp_path)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))
    bundle = ak.context_bundle(task)
    # 已完成步骤进摘要
    assert "media_probe" in bundle
    # 关键数值结构化保留
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
    # 但最近的数值仍在（不能压成一句"更早若干步"就完事）
    assert "duration_sec=199" in bundle


def test_digest_compression_keeps_recent_facts(tmp_path):
    """跑满 24 步后 digest 会被压缩，但最近的数值仍在。"""
    steps = [PlanStep(id=f"s{i}", capability="media_probe",
                      args={"input_path": f"{i}.mp4"}) for i in range(30)]
    planner = RecordingPlanner()

    async def plan(task):  # noqa: ANN001, ANN202
        return Plan(steps=[s.model_copy(deep=True) for s in steps])

    planner.plan = plan  # type: ignore[method-assign]
    k = _kernel(tmp_path, planner)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task.limits.max_turns = 30
    task = asyncio.run(k.run(task))

    assert len(task.context_digest) <= 25, "工作记忆必须有界"
    last = task.context_digest[-1]
    assert last["facts"].get("frames") == 300


# ── 子代理能力（A12）────────────────────────────────────


def test_subagent_capability_refuses_when_mode_off(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "off", raising=False)
    task = _kernel_result()
    out = asyncio.run(sub.run(task, PlanStep(id="s1", capability="spawn_subagent",
                                            args={"role": "核查员"})))
    assert out["status"] == "refused"


def test_subagent_capability_refuses_exec_tools(monkeypatch):
    """最重要的一条：Kernel 不能自己给子代理开 shell 权限。"""
    task = _kernel_result()
    out = asyncio.run(sub.run(task, PlanStep(
        id="s1", capability="spawn_subagent",
        args={"role": "运维", "tools": ["run_shell"]},
    )))
    assert out["status"] == "refused"
    assert "执行权限" in out["error"]
    assert "两级" in out["error"] or "审核" in out["error"]


def test_subagent_capability_requires_role(monkeypatch):
    task = _kernel_result()
    out = asyncio.run(sub.run(task, PlanStep(id="s1", capability="spawn_subagent",
                                            args={"tools": ["read_file"]})))
    assert out["status"] == "refused"
    assert "role" in out["error"]


def test_subagent_capability_spawns_readonly_worker(monkeypatch):
    from clipwright.supervisor import spawn as sp

    captured: dict = {}

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001
        captured.update(kwargs, pid=ctx.pipeline_id)
        return {"subagent_id": "sa_1", "status": "done",
                "output": "第 3 段是占位图", "cost_usd": 0.02, "duration_ms": 1200}

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    task = _kernel_result()
    out = asyncio.run(sub.run(task, PlanStep(
        id="s1", capability="spawn_subagent",
        args={"role": "素材可用性核查员", "system_prompt": "查占位图",
              "tools": ["read_file", "list_dir"]},
    )))
    assert out["status"] == "ok"
    assert out["output"]["subagent_id"] == "sa_1"
    assert out["output"]["cost_usd"] == 0.02
    assert set(captured["tools"]) == {"list_dir", "read_file"}
    assert not set(captured["tools"]) & sub.EXEC_TOOLS


def test_default_executor_routes_spawn_capability(monkeypatch, tmp_path):
    calls: list[str] = []

    async def fake_run(task, step):  # noqa: ANN001
        calls.append(step.capability)
        return {"status": "ok", "output": {"subagent_id": "sa_9"}}

    monkeypatch.setattr(sub, "run", fake_run)

    class P(RecordingPlanner):
        async def plan(self, task):  # noqa: ANN001, ANN201
            return Plan(steps=[PlanStep(id="s1", capability="spawn_subagent",
                                        kind="subagent", args={"role": "核查员"})])

    k = ak.AgentKernel(ak.KernelDeps(planner=P(), executor=ak.default_executor,
                                     checkpoint_dir=tmp_path / "tasks"))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert calls == ["spawn_subagent"]
    assert task.status == "done"


def _kernel_result():
    """一个最小 Task（供能力单测用，不走 Kernel 主循环）。"""
    from clipwright.schema.task import Task

    return Task(id="task_c5", request=TaskRequest(topic="县域经济"))
