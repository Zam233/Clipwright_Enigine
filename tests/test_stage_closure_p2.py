"""P2：自愈闭包 + 老管线下线（PRD docs/master-agent-prd-2026-10-26.md §3.7 / §4.1）。

三件事必须锁死：
1. 重做一个阶段要连带重做它的**下游传递闭包**（否则配音/字幕静默丢失）；
2. `pipeline_enabled=false` 时 `run_pipeline` 能力与 `/api/pipeline/run-async` 一律拒绝；
3. 规划失败的退化计划必须是"按阶段依赖序派 6 个 run_stage"，不是回头打管线。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.services import stage_closure


# ── 闭包语义 ────────────────────────────────────────────────


def test_closure_of_edit_includes_animation_and_audio() -> None:
    assert stage_closure.closure("edit") == ["animation", "audio"]


def test_closure_never_includes_quality() -> None:
    for stage in ("structure", "material", "edit", "animation", "audio"):
        assert "quality" not in stage_closure.closure(stage)


def test_closure_of_audio_is_empty() -> None:
    assert stage_closure.closure("audio") == []


def test_replan_plan_prefixes_redo_stage() -> None:
    assert stage_closure.replan_plan("edit") == ["edit", "animation", "audio"]
    assert stage_closure.replan_plan("structure") == [
        "structure", "material", "edit", "animation", "audio"]
    assert stage_closure.replan_plan("不存在") == []


def test_stage_deps_is_the_single_source_of_truth() -> None:
    """M3：老编排器的 DAG 已删除，`stage_closure.STAGE_DEPS` 是依赖关系的唯一真值。

    这里锁住它的**语义**（不是形状）：每个阶段只依赖上游，`structure` 无依赖。
    """
    from clipwright.services.stage_closure import EXECUTION_ORDER, STAGE_DEPS

    assert STAGE_DEPS["structure"] == ()
    for stage in EXECUTION_ORDER[1:]:
        assert STAGE_DEPS[stage], f"{stage} 必须有上游依赖"
        for dep in STAGE_DEPS[stage]:
            assert EXECUTION_ORDER.index(dep) < EXECUTION_ORDER.index(stage)
def test_capability_replan_closure_returns_stages(monkeypatch) -> None:
    from clipwright.schema.task import PlanStep

    monkeypatch.setattr(settings, "agent_mode", "on")
    step = PlanStep(id="p1", capability="replan_closure", args={"redo_agent": "edit"})
    out = asyncio.run(stage_closure.run(None, step))
    assert out["status"] == "ok"
    assert out["output"]["stages"] == ["edit", "animation", "audio"]
    assert "静默丢失" in out["output"]["reason"]


def test_capability_replan_closure_refuses_without_redo(monkeypatch) -> None:
    from clipwright.schema.task import PlanStep

    monkeypatch.setattr(settings, "agent_mode", "on")
    out = asyncio.run(stage_closure.run(
        None, PlanStep(id="p1", capability="replan_closure", args={})))
    assert out["status"] == "refused"


# ── 老管线下线 ──────────────────────────────────────────────


def _task():
    from clipwright.schema.task import TaskRequest

    class _T:
        id = "task_p2"
        request = TaskRequest(topic="测试", plan_only=False)

    return _T()


def test_run_async_endpoint_returns_410_when_pipeline_disabled(monkeypatch) -> None:
    from clipwright.api import pipeline as pipeline_api

    monkeypatch.setattr(settings, "agent_mode", "off")
    monkeypatch.setattr(settings, "pipeline_enabled", False)

    app = FastAPI()
    app.include_router(pipeline_api.router)
    client = TestClient(app)
    resp = client.post("/api/pipeline/run-async", json={
        "persona_id": "default",
        "category_plugin_id": "knowledge_longform",
        "topic": "x",
    })
    assert resp.status_code == 410
    assert "主 Agent" in resp.json()["detail"]


def test_fallback_plan_is_stage_sequence_not_pipeline() -> None:
    """规划失败时退化为 6 个 run_stage 步骤（而不是回头打管线）。"""
    from clipwright.services.agent_kernel import LLMPlanner
    from clipwright.schema.task import Task, TaskRequest

    task = Task(id="task_fb", request=TaskRequest(topic="x", plan_only=False))

    # 让 LLM 调用必然失败 → 触发退化计划
    import clipwright.services.llm as llm_mod

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no llm")

    orig = llm_mod.LLMService
    try:
        llm_mod.LLMService = _Boom
        plan = asyncio.run(LLMPlanner().plan(task))
    finally:
        llm_mod.LLMService = orig

    caps = [s.capability for s in plan.steps]
    assert caps and set(caps) == {"delegate"}
    stages = [s.args.get("stage") for s in plan.steps]
    assert stages == ["structure", "material", "edit", "animation", "audio", "quality"]
