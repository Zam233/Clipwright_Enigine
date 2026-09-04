"""P8: dry-run 预览模式测试 — 只执行到 edit 粗剪，跳过动画/音频/质检。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
from clipwright.schema.pipeline import PipelineRequest


@pytest.mark.asyncio
async def test_dry_run_stops_after_edit() -> None:
    """dry_run=True → 执行计划被截断到 edit，后续 agent 不执行。"""
    orch = PipelineOrchestratorV2()
    # mock _init 返回轻量上下文
    manifest = MagicMock()
    plugin = MagicMock()
    persona_config = {}
    translated = {}
    agent_context = MagicMock()
    agent_context.extra_params = {}
    orch._init = AsyncMock(return_value=(manifest, plugin, persona_config, translated, agent_context))

    # mock _build_input 返回空输入
    orch._build_input = MagicMock(return_value={})

    # mock _run_agent：仅记录被调用的 agent，返回成功 step
    called: list[str] = []
    real_run = orch._run_agent

    async def fake_run(state, agent_name, input_data, context, bus, tracer=None):
        called.append(agent_name)
        step = MagicMock()
        step.status = MagicMock()
        step.status.value = "completed"
        step.error = None
        step.result = {"timeline": {"tracks": []}}
        step.duration_ms = 1
        step.started_at = None
        step.completed_at = None
        step.model_dump = MagicMock(return_value={})
        state.steps.append(step)
        return step

    orch._run_agent = fake_run  # type: ignore[method-assign]

    state = MagicMock()
    state.pipeline_id = "pl_dry"
    state.status = MagicMock()
    state.status.value = "running"
    state.shared_data = {}
    state.steps = []

    request = PipelineRequest(
        persona_id="p1", category_plugin_id="c1", topic="t", dry_run=True,
    )
    result = await orch._run_inner(request, "pl_dry", state, MagicMock(), None)

    assert "edit" in called
    assert "animation" not in called
    assert "audio" not in called
    assert "quality" not in called
    assert state.shared_data.get("dry_run") is True
    # A9: dry-run 必须产出 final_timeline（与 V1 对齐），否则 /run-async 预览为空
    assert state.shared_data.get("final_timeline") == {"tracks": []}
