"""M2：统一 AgentRun 契约（modern-agent PRD §2.4 / §5.3）。

阶段子 agent 与调研 worker 必须是**同一份契约**：

- 同一 `RunSpec` 输入、同一 `AgentResult` 输出；
- 同一套深度/幂等/取消/成本口径；
- 派发类工具只有一个入口（`surface.execute`），没有第二套能力名解释层；
- 失败后主 agent 拿到的是可决策的结构化原因，不是一句字符串。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from clipwright.schema.run import AgentResult, RunSpec
from clipwright.services import agent_run
from clipwright.tool import surface as sf


def _task(task_id: str = "task_run") -> Any:
    from types import SimpleNamespace

    from clipwright.schema.task import TaskRequest

    return SimpleNamespace(id=task_id, request=TaskRequest(topic="做一期评测"))


@pytest.fixture(autouse=True)
def _clean():
    agent_run.reset()
    from clipwright.services import evaluation, memory

    evaluation.reset()
    memory.reset()
    yield
    agent_run.reset()


def test_depth_guard_refuses_nested_agents():
    spec = RunSpec(kind="stage", stage="edit", depth=2)
    out = asyncio.run(agent_run.run(spec, _task()))
    assert out.status == "refused"
    assert out.failure["category"] == "policy_refused"
    assert "星型拓扑" in out.error


def test_idem_key_replays_without_rework(monkeypatch):
    calls = {"n": 0}

    async def fake_stage(task, spec):  # noqa: ANN001
        calls["n"] += 1
        from clipwright.schema.stage import StageResult

        return StageResult(stage=spec.stage, status="ok", summary="已完成",
                           duration_ms=5)

    from clipwright.services import stage_subagent

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    spec = RunSpec(kind="stage", stage="edit", idem_key="idem-1", depth=1)
    first = asyncio.run(agent_run.run(spec, _task()))
    second = asyncio.run(agent_run.run(spec, _task()))
    assert calls["n"] == 1, "同一 idem_key 不得重复干活"
    assert second.run_id == first.run_id


def test_stage_and_research_share_envelope(monkeypatch):
    from clipwright.schema.stage import StageResult
    from clipwright.services import stage_subagent
    from clipwright.supervisor import spawn as sp

    async def fake_stage(task, spec):  # noqa: ANN001
        return StageResult(stage=spec.stage, status="ok", summary="结构出好了",
                           writes=["docs/plan.md"], cost_usd=0.03, duration_ms=9)

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001, ARG001
        return {"status": "done", "output": "素材够用", "cost_usd": 0.01,
                "tokens": 800, "duration_ms": 4,
                "tool_calls": [{"tool": "read_file", "ok": True}]}

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)

    stage = asyncio.run(agent_run.run(
        RunSpec(kind="stage", stage="structure", depth=1), _task()))
    research = asyncio.run(agent_run.run(
        RunSpec(kind="research", role="核查员", brief="查素材", depth=1), _task()))

    for res in (stage, research):
        assert isinstance(res, AgentResult)
        payload = res.to_tool_payload()
        for key in ("run_id", "kind", "status", "summary", "outputs", "needs",
                    "report_path", "cost_usd", "failure"):
            assert key in payload
    assert stage.kind == "stage" and research.kind == "research"
    assert research.tokens == 800
    assert research.tool_calls[0]["tool"] == "read_file"


def test_metrics_and_episodic_are_recorded(monkeypatch):
    from clipwright.schema.stage import StageResult
    from clipwright.services import evaluation, memory, stage_subagent

    async def fake_stage(task, spec):  # noqa: ANN001
        return StageResult(stage=spec.stage, status="ok", summary="剪辑完成",
                           cost_usd=0.05, duration_ms=11)

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    asyncio.run(agent_run.run(RunSpec(kind="stage", stage="edit", depth=1),
                              _task("task_mem")))

    metrics = evaluation.records("task_mem")
    assert metrics and metrics[0]["status"] == "ok"
    assert metrics[0]["cost_usd"] == 0.05
    hits = memory.recall("剪辑", task_id="task_mem", layers=("episodic",))
    assert hits and hits[0]["kind"] == "outcome"
    assert hits[0]["provenance"]["run_id"]


def test_failure_memory_is_pinned(monkeypatch):
    from clipwright.services import memory, stage_subagent

    async def fake_stage(task, spec):  # noqa: ANN001, ARG001
        raise RuntimeError("ffmpeg 崩了")

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    out = asyncio.run(agent_run.run(RunSpec(kind="stage", stage="edit", depth=1),
                                    _task("task_fail")))
    assert out.status == "failed"
    hits = memory.recall("ffmpeg", task_id="task_fail", layers=("episodic",))
    assert hits and hits[0]["pinned"] is True, "失败样本必须被钉住，便于复盘"


# ── 工具入口唯一性 ─────────────────────────────────────


def test_delegate_agent_goes_through_surface(monkeypatch):
    seen: list[RunSpec] = []

    async def fake_run(spec, task):  # noqa: ANN001
        seen.append(spec)
        return AgentResult(run_id="run_x", kind=spec.kind, stage=spec.stage,
                           status="ok", summary="结构已出")

    monkeypatch.setattr(agent_run, "run", fake_run)
    out = asyncio.run(sf.execute("delegate_agent", {"stage": "structure"},
                                 pipeline_id="task_s", task=_task("task_s")))
    assert out["status"] == "ok"
    assert seen and seen[0].kind == "stage" and seen[0].stage == "structure"
    assert out["output"]["summary"] == "结构已出"
    assert out["record"]["name"] == "delegate_agent"


def test_research_agent_requires_context():
    out = asyncio.run(sf.execute("research_agent", {"brief": "查一下"}))
    assert out["status"] == "refused"
    assert "任务上下文" in out["error"]


def test_legacy_capability_names_are_aliases_not_branches():
    """旧名只是别名表：Kernel 执行器里没有任何 `if capability == ...` 分支。"""
    import inspect

    from clipwright.services import agent_kernel as ak

    src = inspect.getsource(ak.default_executor)
    assert "if step.capability" not in src
    assert "elif" not in src
    assert ak._TOOL_ALIAS["run_stage"] == "delegate_agent"
    assert ak._TOOL_ALIAS["spawn_subagent"] == "research_agent"
    assert ak._TOOL_ALIAS["get_stage"] == "get_agent_run"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
