"""M3 拆除证明（modern-agent PRD §4.1 / §4.3 / §6 J）。

这些用例不测"新功能"，只测**旧的编排残留真的没了**——删除是最容易被"悄悄加回来"
的一类改动，所以用可执行的断言钉住：

- `default_executor` / `_run_step` / `_after_step` / `_run_plan_loop` / `_TOOL_ALIAS` 不存在；
- `Plan` 没有 `next_step()`（没有执行游标）；
- `pipeline_capability` 模块不存在，能力矩阵与工具面都没有 `run_pipeline`；
- 老管线执行入口仍然是 410（历史只读查询还能查）。
"""

from __future__ import annotations

import importlib

import pytest


def test_plan_execution_machinery_is_gone():
    from clipwright.schema.task import Plan
    from clipwright.services import agent_kernel as ak

    for name in ("default_executor", "_run_plan_loop", "_run_step", "_after_step",
                 "_decide", "_reflect", "_observe", "_TOOL_ALIAS", "_GATEWAY_CAPS"):
        assert not hasattr(ak, name), f"{name} 应随计划表执行一起删除"
    assert not hasattr(Plan, "next_step"), "Plan 不再是执行游标"


def test_kernel_mode_only_has_loop_modes():
    from clipwright.services.agent_kernel import AgentKernel, KernelDeps

    k = AgentKernel(KernelDeps(kernel_mode="plan"))
    assert k.kernel_mode == "tool_loop", "plan 形态已下线，只能退化为 tool_loop"


def test_pipeline_capability_module_is_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("clipwright.services.pipeline_capability")


def test_run_pipeline_is_out_of_matrix_and_surface():
    from clipwright.services.capability_matrix import META_CAPABILITIES, TIER_MAP
    from clipwright.tool import surface as sf

    assert "run_pipeline" not in {m["name"] for m in META_CAPABILITIES}
    assert "run_pipeline" not in TIER_MAP
    assert "run_pipeline" not in {e.name for e in sf.build_surface()}
    assert "run_pipeline" not in {s["function"]["name"] for s in sf.schemas()}


def test_pipeline_execution_route_is_410(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import pipeline as pipeline_api
    from clipwright.config import settings

    # agent_mode=on 会把 run-async 转成 Task（兼容层），这里要测的是"老执行入口被删"，
    # 所以把 agent_mode 关掉——与 `pipeline_enabled` 无关，那是本次删掉的开关。
    monkeypatch.setattr(settings, "agent_mode", "off", raising=False)
    monkeypatch.setattr(settings, "pipeline_enabled", True, raising=False)
    app = FastAPI()
    app.include_router(pipeline_api.router)
    resp = TestClient(app).post("/api/pipeline/run-async", json={
        "persona_id": "default", "category_plugin_id": "knowledge_longform",
        "topic": "x",
    })
    assert resp.status_code == 410, "pipeline_enabled=true 也不许把老管线放回来"


def test_agent_capabilities_are_first_class_in_matrix():
    """旧能力名不再是"编排入口"；派发走 agent 工具（矩阵里登记的是工具名）。"""
    from clipwright.services.capability_matrix import META_CAPABILITIES

    names = {m["name"] for m in META_CAPABILITIES}
    assert "delegate_agent" in names or "delegate" in names
    assert "run_pipeline" not in names

def test_capability_adapters_are_gone():
    """B11：两处薄适配层已退役，能力名解析只剩工具面一处真值。"""
    import importlib

    for mod in ("clipwright.services.stage_capability",
                "clipwright.services.subagent_capability"):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{mod} 应已删除")

    # 工具面仍是唯一入口
    from clipwright.tool import surface as sf

    names = {e.name for e in sf.build_surface()}
    assert {"delegate_agent", "research_agent", "get_agent_run",
            "list_agent_runs", "cancel_agent_run"} <= names


def test_subagent_tool_policy_lives_in_runtime():
    """只读工具策略必须留在运行时（不能随适配层一起消失）。"""
    from clipwright.services import agent_run

    assert agent_run.EXEC_TOOLS and agent_run.READONLY_TOOLS
    with pytest.raises(agent_run.RunRefused):
        agent_run.enforce_tool_policy(
            agent_run.RunSpec(kind="research", tools=["run_shell"]))
    ok = agent_run.enforce_tool_policy(
        agent_run.RunSpec(kind="research", tools=["read_file", "web_search"]))
    assert ok == ["read_file", "web_search"]

