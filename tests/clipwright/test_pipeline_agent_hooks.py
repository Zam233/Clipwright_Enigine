"""SA-1: PRE_AGENT / POST_AGENT Hook 接线回归测试。

覆盖：PRE 改写 input / PRE skip / POST 观测 / 异常路径 POST / 注销后失效 /
Hook 异常不阻塞执行。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.plugins.hooks import HookPoint, HookRegistry
from clipwright.schema.agent import AgentContext, AgentDecision
from clipwright.schema.pipeline import PipelineRequest, PipelineState
from clipwright.services.agent_bus import AgentBus
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2


@pytest.fixture(autouse=True)
def _clean_hooks():
    HookRegistry.clear()
    yield
    HookRegistry.clear()


def _make_orch(result=None, exc: Exception | None = None):
    """构造编排器并桩掉 _dispatch，捕获收到的 input_data。"""
    orch = PipelineOrchestratorV2()
    captured: dict = {}

    async def fake_dispatch(name, input_data, context):
        captured["name"] = name
        captured["input"] = input_data
        if exc is not None:
            raise exc
        return result

    orch._dispatch = fake_dispatch  # type: ignore[method-assign]
    return orch, captured


def _make_state() -> PipelineState:
    req = PipelineRequest(persona_id="p", category_plugin_id="c", topic="t")
    return PipelineState(pipeline_id="pl_hooktest", request=req)


def _make_ctx() -> AgentContext:
    return AgentContext(pipeline_id="pl_hooktest", persona_id="p",
                        category_plugin_id="c", topic="t", extra_params={})


_PASS = SimpleNamespace(decision=AgentDecision.PASS, error=None)


@pytest.mark.asyncio
async def test_pre_agent_hook_rewrites_input() -> None:
    """PRE Hook 改写 input → dispatch 收到改写后的数据。"""
    seen: list[dict] = []

    def pre(ctx):
        seen.append(dict(ctx["input"]))
        return {"input": {**ctx["input"], "injected": True}}

    HookRegistry.register(HookPoint.PRE_AGENT, pre)
    orch, captured = _make_orch(_PASS)
    state = _make_state()

    step = await orch._run_agent(state, "structure", {"k": "v"}, _make_ctx(), AgentBus("pl_hooktest"))

    assert step.status.value == "completed"
    assert captured["input"]["injected"] is True
    assert captured["input"]["k"] == "v"
    assert seen == [{"k": "v"}], "Hook 收到的应是原始 input 副本"


@pytest.mark.asyncio
async def test_pre_agent_hook_skips_execution() -> None:
    """PRE Hook skip → dispatch 不执行，step 置跳过语义。"""
    HookRegistry.register(
        HookPoint.PRE_AGENT,
        lambda ctx: {"skip": True, "reason": "测试跳过"},
    )
    orch, captured = _make_orch(_PASS)
    state = _make_state()

    step = await orch._run_agent(state, "structure", {"k": "v"}, _make_ctx(), AgentBus("pl_hooktest"))

    assert "input" not in captured, "skip 后 dispatch 不应执行"
    assert step.status.value == "cancelled"
    assert "测试跳过" in (step.error or "")


@pytest.mark.asyncio
async def test_post_agent_hook_observes_decision() -> None:
    """POST Hook 收到 decision/result/error（只读观测）。"""
    observed: list[dict] = []

    def post(ctx):
        observed.append({
            "decision": ctx["decision"],
            "result_keys": sorted((ctx["result"] or {}).keys()),
            "error": ctx["error"],
        })

    HookRegistry.register(HookPoint.POST_AGENT, post)
    result = SimpleNamespace(decision=AgentDecision.PASS, error=None, payload={"a": 1})
    result.model_dump = lambda mode="json": {"payload": {"a": 1}}  # type: ignore[method-assign]
    orch, _ = _make_orch(result)

    await orch._run_agent(_make_state(), "structure", {}, _make_ctx(), AgentBus("pl_hooktest"))

    assert len(observed) == 1
    assert "PASS" in observed[0]["decision"]
    assert observed[0]["error"] == ""


@pytest.mark.asyncio
async def test_post_agent_hook_fires_on_exception() -> None:
    """dispatch 异常 → POST Hook 仍触发（带 error），step FAILED。"""
    observed: list[dict] = []

    def post(ctx):
        observed.append({"decision": ctx["decision"], "error": ctx["error"]})

    HookRegistry.register(HookPoint.POST_AGENT, post)
    orch, captured = _make_orch(exc=RuntimeError("boom"))

    step = await orch._run_agent(_make_state(), "structure", {}, _make_ctx(), AgentBus("pl_hooktest"))

    assert step.status.value == "failed"
    assert len(observed) == 1
    assert "FAIL" in observed[0]["decision"]
    assert "boom" in observed[0]["error"]


@pytest.mark.asyncio
async def test_unregister_plugin_stops_hook() -> None:
    """插件注销后其 Hook 不再触发（P4 清理语义对齐）。"""
    calls: list[dict] = []

    def pre(ctx):
        calls.append(dict(ctx["input"]))
        return {"input": {**ctx["input"], "injected": True}}

    HookRegistry.register(HookPoint.PRE_AGENT, pre, plugin_id="plug_a")
    orch, captured = _make_orch(_PASS)
    state = _make_state()
    ctx = _make_ctx()

    await orch._run_agent(state, "structure", {"k": "v"}, ctx, AgentBus("pl_hooktest"))
    assert captured["input"].get("injected") is True

    removed = HookRegistry.unregister_plugin("plug_a")
    assert removed == 1

    orch2, captured2 = _make_orch(_PASS)
    await orch2._run_agent(_make_state(), "structure", {"k": "v"}, ctx, AgentBus("pl_hooktest"))
    assert calls and "injected" not in captured2["input"]


@pytest.mark.asyncio
async def test_hook_exception_does_not_block_execution() -> None:
    """单钩子异常被隔离（P8），其余钩子照常生效，执行不中断。"""
    def bad(_ctx):
        raise RuntimeError("hook exploded")

    def good(ctx):
        return {"input": {**ctx["input"], "good": True}}

    HookRegistry.register(HookPoint.PRE_AGENT, bad)
    HookRegistry.register(HookPoint.PRE_AGENT, good)
    orch, captured = _make_orch(_PASS)

    step = await orch._run_agent(_make_state(), "structure", {"k": "v"}, _make_ctx(), AgentBus("pl_hooktest"))

    assert step.status.value == "completed"
    assert captured["input"].get("good") is True
