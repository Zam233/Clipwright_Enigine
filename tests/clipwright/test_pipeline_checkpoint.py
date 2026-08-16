"""C1: 断点续跑落库 — 每完成一个 agent 即持久化检查点测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.services.pipeline_v2 import PipelineOrchestratorV2


@pytest.mark.asyncio
async def test_run_agent_persists_checkpoint_after_step() -> None:
    """_run_agent 完成后调用 _persist_state（检查点落库）。"""
    orch = PipelineOrchestratorV2()
    # 用 mock agent 直接返回成功，避免真实 LLM/工具调用
    mock_agent = MagicMock()
    mock_agent.execute = AsyncMock()
    result = MagicMock()
    result.decision = MagicMock()
    result.decision.value = "pass"
    result.error = None
    result.model_dump.return_value = {"ok": True, "tool_calls": [], "llm_calls": []}
    mock_agent.execute.return_value = result
    orch._agents["structure"] = mock_agent

    state = MagicMock()
    state.pipeline_id = "pl_c1_checkpoint"
    state.current_agent = "structure"
    state.shared_data = {}
    state.steps = []
    state.status = MagicMock()
    state.status.value = "running"
    state.add_step = MagicMock(return_value=MagicMock(
        status=MagicMock(value="running"),
        started_at=None, completed_at=None, duration_ms=0,
    ))
    state.updated_at = MagicMock()

    persist = AsyncMock()
    with patch.object(orch, "_persist_state", persist):
        await orch._run_agent(
            state, "structure", {"persona_config": {}}, MagicMock(), MagicMock(),
        )

    # 检查点持久化被调用（C1 核心断言；经 run_in_executor 线程执行，故用 called）
    persist.assert_called_once()
    args = persist.call_args
    assert args.args[0] is state
