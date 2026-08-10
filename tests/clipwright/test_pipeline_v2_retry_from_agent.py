"""B3: retry 从失败 agent 恢复 — 只重跑目标 + 下游，不重跑已成功前置。

验证 PipelineOrchestratorV2.run_from_agent：
- 从 prior_state.steps 重放目标 agent 之前成功步骤 → result_data；
- 只重跑目标 agent + 下游联动（_get_downstream_agents）；
- quality 走统一自愈循环（恰好 1 次）；
- 目标 agent 无可用前置结果 / 未找到时抛 ValueError（端点映射 400）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from clipwright.schema.pipeline import PipelineRequest, PipelineState, PipelineStatus
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2


def _make_result(name: str) -> dict[str, Any]:
    if name == "structure":
        return {"scenes": [{"title": "s1", "duration_sec": 10.0}], "topic": "测试"}
    if name == "material":
        return {"candidate_clips": []}
    if name == "edit":
        return {
            "timeline": {
                "id": "tl", "width": 1920, "height": 1080, "fps": 30,
                "duration_sec": 10.0, "tracks": [],
            },
        }
    if name == "animation":
        return {"animation_notes": ["ok"]}
    if name == "audio":
        return {"audio_notes": ["ok"]}
    return {"redo_agent": "", "issues": []}


def _make_prior_state() -> dict:
    """部分成功 state：structure+material 完成、edit 失败、下游 pending。"""
    return {
        "steps": [
            {"agent_name": "structure", "status": "completed", "result": _make_result("structure"), "error": None},
            {"agent_name": "material", "status": "completed", "result": _make_result("material"), "error": None},
            {"agent_name": "edit", "status": "failed", "result": None, "error": "edit boom"},
            {"agent_name": "animation", "status": "failed", "result": None, "error": "no timeline"},
            {"agent_name": "audio", "status": "pending", "result": None, "error": None},
            {"agent_name": "quality", "status": "pending", "result": None, "error": None},
        ],
        "request": {
            "persona_id": "default",
            "category_plugin_id": "knowledge_longform",
            "topic": "测试",
            "extra_params": {},
        },
    }


def _build_orch(run_log: list[str]) -> PipelineOrchestratorV2:
    orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    orch._agents = {}

    async def fake_init(request, pid, state, bus):
        manifest = SimpleNamespace(parameter=None, prompt="")
        plugin = SimpleNamespace(config={})
        return manifest, plugin, {}, {}, SimpleNamespace(extra_params={})

    async def fake_run_agent(state, agent_name, input_data, context, bus, tracer=None):
        run_log.append(agent_name)
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
async def test_retry_reruns_target_and_downstream_only() -> None:
    run_log: list[str] = []
    orch = _build_orch(run_log)
    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="测试",
    )
    prior = _make_prior_state()
    result = await orch.run_from_agent("pl_retry", req, "edit", prior_state=prior)

    assert result.status == PipelineStatus.COMPLETED
    # structure/material 不重跑；edit + 下游(animation→audio) 重跑；quality 恰好 1 次
    assert run_log == ["edit", "animation", "audio", "quality"]


@pytest.mark.asyncio
async def test_retry_target_not_found_raises() -> None:
    orch = _build_orch([])
    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="测试",
    )
    prior = _make_prior_state()
    with pytest.raises(ValueError):
        await orch.run_from_agent("pl_retry", req, "nonexistent", prior_state=prior)


@pytest.mark.asyncio
async def test_retry_no_preceding_result_raises() -> None:
    orch = _build_orch([])
    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="测试",
    )
    prior = {
        "steps": [
            {"agent_name": "edit", "status": "failed", "result": None, "error": "x"},
        ],
        "request": {"persona_id": "default", "category_plugin_id": "knowledge_longform", "topic": "测试"},
    }
    with pytest.raises(ValueError):
        await orch.run_from_agent("pl_retry", req, "edit", prior_state=prior)


@pytest.mark.asyncio
async def test_retry_replays_preceding_results_into_build_input() -> None:
    """目标 agent 的 input_data 应包含重放的前置结果（scenes/candidate_clips）。"""
    captured: list[dict] = []

    orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
    orch._agents = {}

    async def fake_init(request, pid, state, bus):
        manifest = SimpleNamespace(parameter=None, prompt="")
        plugin = SimpleNamespace(config={})
        return manifest, plugin, {}, {}, SimpleNamespace(extra_params={})

    async def fake_run_agent(state, agent_name, input_data, context, bus, tracer=None):
        captured.append(input_data)
        step = state.add_step(agent_name)
        step.status = PipelineStatus.COMPLETED
        step.result = _make_result(agent_name)
        return step

    orch._init = fake_init
    orch._run_agent = fake_run_agent
    orch._persona_prompt = ""
    orch._vision_prompt = ""
    orch._rag_context = ""

    req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="测试",
    )
    prior = _make_prior_state()
    await orch.run_from_agent("pl_retry", req, "edit", prior_state=prior)

    # edit 的 input_data 应包含重放后的 script_skeleton（scenes 来自 structure 结果）
    edit_input = captured[0]
    scenes = edit_input.get("script_skeleton", {}).get("scenes", [])
    assert len(scenes) == 1
    assert scenes[0]["title"] == "s1"
