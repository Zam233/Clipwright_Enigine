"""G2: 协作式取消管线 — cancel 标记后下一个 agent 边界生效，终态 CANCELLED。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.schema.agent import AgentDecision
from clipwright.schema.pipeline import PipelineRequest, PipelineState, PipelineStatus
from clipwright.services.pipeline_v2 import (
    PipelineOrchestratorV2,
    clear_cancel,
    is_cancelled,
    mark_cancelled,
)


@pytest.fixture(autouse=True)
def _clean_cancel():
    clear_cancel()
    yield
    clear_cancel()


class TestCancelFlag:
    def test_mark_and_check(self) -> None:
        assert is_cancelled("pl_x") is False
        mark_cancelled("pl_x")
        assert is_cancelled("pl_x") is True
        clear_cancel()
        assert is_cancelled("pl_x") is False


def _build_orch(run_log: list[str]) -> PipelineOrchestratorV2:
    orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    orch._agents = {}

    async def fake_init(request, pid, state, bus):
        manifest = SimpleNamespace(parameter=None, prompt="")
        plugin = SimpleNamespace(config={})
        return manifest, plugin, {}, {}, SimpleNamespace(extra_params={})

    async def fake_dispatch(name, data, ctx):
        run_log.append(name)
        return SimpleNamespace(
            decision=AgentDecision.PASS,
            model_dump=lambda mode="json": {"note": "ok"},
        )

    orch._init = fake_init
    orch._dispatch = fake_dispatch
    orch._persona_prompt = ""
    orch._vision_prompt = ""
    orch._rag_context = ""
    return orch


@pytest.mark.asyncio
async def test_cancelled_agent_returns_skip() -> None:
    """取消标记后，_run_agent 在 dispatch 前返回 SKIP 且不执行真实 agent。"""
    run_log: list[str] = []
    orch = _build_orch(run_log)
    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="t",
    )
    state = PipelineState(pipeline_id="pl_cancel_1", request=req)
    from clipwright.services.agent_bus import AgentBus
    bus = AgentBus("pl_cancel_1")
    ctx = SimpleNamespace(extra_params={}, pipeline_id="pl_cancel_1")

    # 未取消 → 正常执行
    mark_cancelled("pl_cancel_1")
    step = await orch._run_agent(state, "structure", {"persona_config": {}}, ctx, bus)
    assert step.status == PipelineStatus.CANCELLED
    assert run_log == []  # 未进入 dispatch


@pytest.mark.asyncio
async def test_cancel_marks_pipeline_cancelled_status() -> None:
    """run() 完成后若被取消 → state.status=CANCELLED + result 写 cancelled。"""
    orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    orch._agents = {}

    async def fake_run_inner(request, pid, state, bus, tracer, task_id):
        state.status = PipelineStatus.COMPLETED
        return state

    orch._run_inner = fake_run_inner  # type: ignore[method-assign]
    from clipwright.services.agent_bus import AgentBus

    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="t",
    )
    mark_cancelled("pl_cancel_2")
    try:
        state = await orch.run(req, pipeline_id="pl_cancel_2")
    finally:
        clear_cancel()

    assert state.status == PipelineStatus.CANCELLED


@pytest.mark.asyncio
async def test_not_cancelled_normal_completion() -> None:
    """未取消 → 正常完成。"""
    orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    orch._agents = {}

    async def fake_run_inner(request, pid, state, bus, tracer, task_id):
        state.status = PipelineStatus.COMPLETED
        return state

    orch._run_inner = fake_run_inner  # type: ignore[method-assign]
    from clipwright.services.agent_bus import AgentBus

    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="t",
    )
    state = await orch.run(req, pipeline_id="pl_cancel_3")
    assert state.status == PipelineStatus.COMPLETED
