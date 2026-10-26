"""第二批（PRD §20.4）：D5/D6/D7、inputs 显式传参、真取消、思考事件、收尾守门。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from clipwright.config import settings
from clipwright.schema.stage import StageSpec
from clipwright.schema.task import PlanStep, Task, TaskRequest
from clipwright.services import stage_inputs, stage_merge
from clipwright.services import stage_subagent
from clipwright.services import task_blackboard as bb


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspaces")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    bb.reset()
    stage_subagent.reset()


def _task(task_id="t_batch2", root: Path | None = None) -> Task:
    extra = {"workspace_root": str(root)} if root else {}
    return Task(id=task_id, request=TaskRequest(topic="x", plan_only=False, extra=extra))


# ── D5：edit 空场景失败要带 failure ────────────────────────


def test_edit_empty_scenes_reports_input_insufficient() -> None:
    from clipwright.agents.edit_agent import EditAgent
    from clipwright.schema.agent import AgentContext, EditInput

    ctx = AgentContext(pipeline_id="p", persona_id="default",
                       category_plugin_id="knowledge_longform", topic="x")
    out = asyncio.run(EditAgent().execute(
        EditInput(context=ctx, script_skeleton={"scenes": []}, candidate_clips=[]), ctx))
    assert str(out.decision).endswith("FAIL")
    assert out.failure is not None
    assert out.failure.category == "input_insufficient"
    assert out.failure.needs and out.failure.needs[0]["from_stage"] == "structure"
    assert out.timeline is None            # D6 的前置：失败不返回空壳时间线
    assert out.error                     # 原文原因要带出来


# ── D6：失败阶段的空时间线不入库 ──────────────────────────


def test_merge_skips_empty_timeline_on_failure() -> None:
    written, errors = stage_merge.merge(
        "edit",
        {"decision": "fail", "timeline": {"tracks": []}, "error": "空场景"},
        task_id="t_d6",
    )
    assert "timeline" not in written
    assert any("timeline" in e for e in errors)
    assert "timeline" not in bb.get("t_d6")


# ── D7：material 假成功 ───────────────────────────────────


def test_material_empty_scenes_is_failure() -> None:
    from clipwright.agents.material_agent import MaterialAgent
    from clipwright.schema.agent import AgentContext, MaterialInput

    ctx = AgentContext(pipeline_id="p", persona_id="default",
                       category_plugin_id="knowledge_longform", topic="x")
    out = asyncio.run(MaterialAgent().execute(
        MaterialInput(context=ctx, script_skeleton={"scenes": []}), ctx))
    assert str(out.decision).endswith("FAIL")
    assert out.failure is not None and out.failure.category == "input_insufficient"


def test_material_without_sources_passes_with_degradation() -> None:
    from clipwright.agents.material_agent import MaterialAgent
    from clipwright.schema.agent import AgentContext, MaterialInput

    ctx = AgentContext(pipeline_id="p", persona_id="default",
                       category_plugin_id="knowledge_longform", topic="x")
    out = asyncio.run(MaterialAgent().execute(
        MaterialInput(context=ctx, script_skeleton={"scenes": [{"title": "s1"}]}), ctx))
    assert str(out.decision).endswith("PASS")
    assert any(d.kind == "material_placeholder" for d in out.degradations)


# ── S2 步进：inputs 显式传参不读黑板 ───────────────────────


def test_explicit_inputs_bypass_blackboard(monkeypatch) -> None:
    class _FakeAgent:
        async def execute(self, input_obj, context):
            return {"scenes": [{"title": "s1"}], "decision": "pass"}

    seen: dict[str, object] = {}

    def _fake_agent_for(stage):
        class _Spy:
            async def execute(self, input_obj, context):
                seen["input"] = input_obj
                return {"scenes": [{"title": "s1"}]}
        return _Spy()

    monkeypatch.setattr(stage_subagent, "agent_for", _fake_agent_for)
    task = _task("t_inputs")

    # 黑板上故意放一份"会污染"的场景
    bb.put(task.id, "script_skeleton", {"scenes": [{"title": "黑板场景"}]}, by="structure")
    explicit = {"script_skeleton": {"scenes": [{"title": "主 agent 指定的"}]}}
    result = asyncio.run(stage_subagent.run_stage(
        task, StageSpec(stage="material", inputs=explicit)))
    assert result.status == "ok"
    assert getattr(seen["input"], "script_skeleton")["scenes"][0]["title"] == "主 agent 指定的"


def test_explicit_inputs_missing_returns_needs() -> None:
    task = _task("t_inputs2")
    bb.put(task.id, "script_skeleton", {"scenes": [{"title": "x"}]}, by="structure")
    # 显式给了 candidate_clips 但没给 script_skeleton → 必须报 needs，而不是回头读黑板
    result = asyncio.run(stage_subagent.run_stage(
        task, StageSpec(stage="edit", inputs={"candidate_clips": []})))
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "input_insufficient"
    assert any(n["field"] == "script_skeleton" for n in result.failure.needs)


def test_validate_reports_missing_fields() -> None:
    needs = stage_inputs.validate("edit", {"candidate_clips": []})
    assert [n["field"] for n in needs] == ["script_skeleton"]
    assert needs[0]["from_stage"] == "structure"


# ── S5：真取消 ────────────────────────────────────────────


def test_cancel_interrupts_running_task(monkeypatch, tmp_path) -> None:
    from clipwright.services.agent_kernel import AgentKernel

    async def _slow_executor(task, step):
        await asyncio.sleep(30)
        return {"status": "ok"}

    async def _run() -> None:
        kernel = AgentKernel()
        kernel.deps.executor = _slow_executor  # type: ignore[assignment]
        task = kernel.create(TaskRequest(topic="慢任务", plan_only=False), mode="on")
        task.plan = __import__("clipwright.schema.task", fromlist=["Plan"]).Plan(
            steps=[PlanStep(id="p1", capability="delegate", args={"stage": "structure"})])
        running = asyncio.create_task(kernel.run(task))
        await asyncio.sleep(0.2)
        kernel.cancel(task.id)
        try:
            await asyncio.wait_for(running, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            # 协程被 cancel 打断是**预期行为**（真取消），不是测试失败
            pass
        assert task.status in ("cancelled", "failed")
        assert running.done()     # 5s 内确实结束了，而不是还在跑

    asyncio.run(_run())


# ── S5：收尾守门（创作流程无时间线不算完成）────────────────


def test_finish_gate_requires_timeline_for_creation_flow() -> None:
    from clipwright.services.agent_kernel import AgentKernel

    kernel = AgentKernel()
    task = _task("t_gate")
    from clipwright.schema.task import Plan

    task.plan = Plan(steps=[PlanStep(id="p1", capability="delegate",
                                     args={"stage": "structure"}, status="done")])
    kernel._finish(task, reason="plan_completed")  # noqa: SLF001
    assert task.status == "failed"
    assert "时间线" in (task.finish.reason if task.finish else "")


# ── S3：思考事件 ──────────────────────────────────────────


def test_thinking_event_emitted_when_reasoning_present(monkeypatch) -> None:
    from clipwright.services import agent_kernel, llm
    from clipwright.services.task_events import events

    llm._remember_reasoning("我先看看素材库里有什么")  # noqa: SLF001
    task = _task("t_think")
    step = PlanStep(id="p1", capability="delegate", args={"stage": "structure"})
    agent_kernel._emit_thinking(task, step)  # noqa: SLF001
    items = [e for e in events(task.id) if e.type == "thinking"]
    assert items and "素材库" in items[-1].detail["delta"]
