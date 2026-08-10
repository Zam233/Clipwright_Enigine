"""B1: Quality agent runs exactly once per pipeline (normal path).

PipelineV2 的 DAG 执行计划包含 quality（依赖 audio/animation），
自愈 while 循环又无条件先跑一次 quality —— 导致正常路径下 quality 执行两次。
本测试断言：一次管线内 quality step 恰好出现 1 次（正常路径）；
自愈路径下为 1+heal 次（不重复主循环那次）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from clipwright.schema.pipeline import (
    PipelineRequest,
    PipelineState,
    PipelineStatus,
    PipelineStep,
)
from clipwright.services.agent_bus import AgentBus
from clipwright.services.pipeline_v2 import AgentDAG, PipelineOrchestratorV2


def _count_quality(steps: list[PipelineStep]) -> int:
    return sum(1 for s in steps if s.agent_name == "quality")


def _fake_agent_context() -> SimpleNamespace:
    return SimpleNamespace(extra_params={})


def _make_result(name: str) -> dict[str, Any]:
    """构造各 Agent 的成功 result（供 result_data 累积）。"""
    if name == "structure":
        return {"scenes": [{"title": "s1", "duration_sec": 10.0}], "topic": "测试"}
    if name == "material":
        return {"candidate_clips": []}
    if name == "edit":
        return {
            "timeline": {
                "id": "tl1", "width": 1920, "height": 1080, "fps": 30,
                "duration_sec": 10.0, "tracks": [],
            },
        }
    if name == "animation":
        return {"animation_notes": ["ok"]}
    if name == "audio":
        return {"audio_notes": ["ok"]}
    # quality 通过（无 error severity 问题 → 不触发自愈）
    return {"redo_agent": "", "issues": []}


def _build_orchestrator() -> PipelineOrchestratorV2:
    """构造编排器并 stub _init / _run_agent，避免真实 Persona/LLM 调用。"""
    orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    orch._agents = {}

    async def fake_init(request, pid, state, bus):
        manifest = SimpleNamespace(parameter=None, prompt="")
        plugin = SimpleNamespace(config={})
        return manifest, plugin, {}, {}, _fake_agent_context()

    async def fake_run_agent(state, agent_name, input_data, context, bus, tracer=None):
        step = state.add_step(agent_name)
        step.status = PipelineStatus.COMPLETED
        step.result = _make_result(agent_name)
        return step

    orch._init = fake_init
    orch._run_agent = fake_run_agent
    orch._persona_prompt = ""
    orch._vision_prompt = ""
    orch._rag_context = ""
    return orch


@pytest.mark.asyncio
async def test_quality_runs_exactly_once_normal_path() -> None:
    """正常路径：quality 在管线内恰好执行 1 次。"""
    orch = _build_orchestrator()
    req = PipelineRequest(
        persona_id="default",
        category_plugin_id="knowledge_longform",
        topic="测试主题",
    )
    state = PipelineState(pipeline_id="pl_test", request=req)
    bus = AgentBus("pl_test")
    result = await orch._run_inner(req, "pl_test", state, bus)

    assert result.status == PipelineStatus.COMPLETED
    assert _count_quality(result.steps) == 1


@pytest.mark.asyncio
async def test_quality_runs_once_plus_heal_when_self_healing() -> None:
    """自愈路径：quality 执行 1（主循环）+ heal 次，不重复主循环那次。"""
    orch = _build_orchestrator()

    call_log: list[str] = []
    quality_calls = 0

    async def fake_run_agent(state, agent_name, input_data, context, bus, tracer=None):
        nonlocal quality_calls
        call_log.append(agent_name)
        step = state.add_step(agent_name)
        step.status = PipelineStatus.COMPLETED
        if agent_name == "quality":
            quality_calls += 1
            if quality_calls == 1:
                # 第一次 quality 检出 error → 触发一次自愈重做 animation
                step.result = {"redo_agent": "animation", "issues": [
                    {"severity": "error", "message": "动画不够"},
                ]}
            else:
                step.result = _make_result(agent_name)
        else:
            step.result = _make_result(agent_name)
        return step

    orch._init = _init_same
    orch._run_agent = fake_run_agent
    orch._persona_prompt = ""
    orch._vision_prompt = ""
    orch._rag_context = ""

    req = PipelineRequest(
        persona_id="default",
        category_plugin_id="knowledge_longform",
        topic="测试主题",
    )
    state = PipelineState(pipeline_id="pl_test2", request=req)
    bus = AgentBus("pl_test2")
    result = await orch._run_inner(req, "pl_test2", state, bus)

    # 自愈后 quality 通过 → 主循环 quality 1 次 + 自愈重跑后 1 次 = 2 次
    assert _count_quality(result.steps) == 2


async def _init_same(request, pid, state, bus):
    manifest = SimpleNamespace(parameter=None, prompt="")
    plugin = SimpleNamespace(config={})
    return manifest, plugin, {}, {}, _fake_agent_context()


def test_execution_plan_excludes_quality() -> None:
    """DAG 执行计划不应包含 quality（统一由自愈循环调度）。"""
    plan = AgentDAG.get_execution_plan()
    flat = [a for group in plan for a in group]
    assert "quality" not in flat
    assert "audio" in flat
