"""B4：Task trace 因果信封闭环（closeout plan B4 / PRD §3.1 / §2.7）。

三件事必须同时成立，否则 trace 只是"有一堆事件"：

1. `tool_result` 指向**它对应的** `tool_call`（`causation_id = seq:N`）；
2. 子 run 的进度/等审核/回放要有 `agent_run_update`（旧实现只声明未发）；
3. 死代码 `RunEvent` 删除——事件源只有一个（`TaskEvent`），不要两套信封。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from clipwright.schema.task import TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import agent_loop, agent_run, task_events as te
from clipwright.tool import surface as sf


class _FakeProvider:
    async def with_tools(self, *, system_prompt, user_prompt, tool_executor, tools,  # noqa: ANN001, ARG002
                         max_tool_rounds=10, **kw):
        await tool_executor("media_probe", {"input_path": "a.mp4"})

        class _R:
            content = "完事"
            tool_calls: list = []
            success = True
            status_code = 200

        return _R()


def _task_obj(task_id: str = "task_trace"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=task_id, request=TaskRequest(topic="x"), plan=None,
        limits=SimpleNamespace(max_turns=10, max_tool_calls=20, max_wall_sec=600,
                               budget_usd=None),
        usage=SimpleNamespace(turns=0, tool_calls=0, wall_sec=0.0, cost_usd=0.0),
    )


@pytest.fixture(autouse=True)
def _clean():
    te.clear()
    agent_run.reset()
    yield
    te.clear()
    agent_run.reset()


def test_tool_result_points_at_its_tool_call(monkeypatch):
    async def fake_execute(name, args=None, **kw):  # noqa: ANN001, ARG001
        return {"status": "ok", "output": {"n": 1}}

    monkeypatch.setattr(sf, "execute", fake_execute)
    import clipwright.services.llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLMService", lambda *a, **k: _FakeProvider())

    asyncio.run(agent_loop.run_loop(_task_obj(), context_text=""))

    evs = te.events("task_trace")
    calls = [e for e in evs if e.type == "tool_call"]
    results = [e for e in evs if e.type == "tool_result"]
    assert calls and results
    assert results[0].causation_id == f"seq:{calls[0].seq}"
    # trace_id 落到任务粒度，前端可以整体串起来
    assert all(e.trace_id == "task_trace" for e in evs)


def test_agent_run_update_emitted_for_review(monkeypatch):
    """等人工审核要发 update（不是 end），否则前端只看到"卡住"。"""
    async def fake_stage(task, spec):  # noqa: ANN001
        from clipwright.schema.stage import StageResult

        return StageResult(stage=spec.stage, status="ok", summary="需要人看一眼")

    from clipwright.services import stage_subagent

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)

    # 直接构造 needs 结果路径：让 research adapter 走 pending_review
    from clipwright.supervisor import spawn as sp

    async def fake_spawn(ctx, **kw):  # noqa: ANN001, ARG002
        return {"status": "pending_review", "worker_id": "w1",
                "review": {"risk": "high", "reasons": ["会删目录"]}}

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)

    from types import SimpleNamespace

    task = SimpleNamespace(id="task_upd", request=TaskRequest(topic="x"))
    asyncio.run(agent_run.run(
        agent_run.RunSpec(kind="research", role="运维核查员", brief="查一下"),
        task))

    types = [e.type for e in te.events("task_upd")]
    assert "agent_run_start" in types and "agent_run_update" in types
    upd = [e for e in te.events("task_upd") if e.type == "agent_run_update"][0]
    assert upd.detail.get("status") == "needs_review"
    assert upd.run_id, "update 事件必须带 run_id"


def test_idem_replay_emits_update(monkeypatch):
    """幂等复用要留痕：否则 trace 上看起来"又干了一遍"。"""
    from clipwright.schema.stage import StageResult
    from clipwright.services import stage_subagent

    async def fake_stage(task, spec):  # noqa: ANN001
        return StageResult(stage=spec.stage, status="ok", summary="做完了")

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    from types import SimpleNamespace

    task = SimpleNamespace(id="task_idem", request=TaskRequest(topic="x"))
    spec = agent_run.RunSpec(kind="stage", stage="edit", idem_key="k1")
    asyncio.run(agent_run.run(spec, task))
    asyncio.run(agent_run.run(spec, task))

    ups = [e for e in te.events("task_idem") if e.type == "agent_run_update"]
    assert ups and ups[0].detail.get("replayed") is True


def test_run_event_schema_removed():
    """事件源只能有一个：死代码 `RunEvent` 不应再存在。"""
    import clipwright.schema.run as run_schema

    assert not hasattr(run_schema, "RunEvent")


def test_kernel_end_events_carry_span(tmp_path):
    class _D:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None, dry_run=False):  # noqa: ANN001
            return {"status": "ok", "finish": {"conclusion": "ok"},
                    "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}

    kernel = ak.AgentKernel(ak.KernelDeps(driver=_D(),
                                          checkpoint_dir=tmp_path / "tasks"))
    task = kernel.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(kernel.run(task))
    ends = [e for e in te.events(task.id) if e.type == "task_end"]
    assert ends and ends[0].span == "kernel"
    assert ends[0].trace_id == task.id
