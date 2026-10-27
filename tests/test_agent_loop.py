"""M1：Kernel 原生 tool-use loop（modern-agent PRD §2.2 / §5.2）。

测的是**可证伪**的东西，不是"日志看起来像 agent"：

- 每轮请求都带 `tools`，且工具面里包含派发类工具；
- 模型发 tool_call → 真的执行 → 结果回填 → 模型继续；
- 闸（取消/预算/墙钟）在**工具执行前**生效，中止后 Kernel 可恢复；
- shadow 模式记录调用序列但不产生副作用；
- 条件不具备时**明确降级**回 plan adapter，不假装在跑 loop。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from clipwright.services import agent_loop
from clipwright.tool import surface as sf


class _FakeResp:
    def __init__(self, content: str = "", tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.success = True
        self.status_code = 200


class _FakeLLM:
    """假 provider：记录每轮收到的 tools，按脚本回放 tool_calls。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.rounds: list[list[dict[str, Any]]] = []
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def with_tools(self, *, system_prompt, user_prompt, tool_executor, tools,
                         max_tool_rounds=10, **kwargs):  # noqa: ANN001, ARG002
        self.rounds.append(list(tools))
        pending = list(self.script)
        await pending_calls(self, tool_executor, pending)
        return _FakeResp(content="本轮结论：已完成可做的部分")


async def pending_calls(fake: _FakeLLM, executor: Any, pending: list[Any]) -> None:
    for item in pending:
        fake.executed.append(item)
        await executor(item[0], item[1] or {})


def _task() -> Any:
    from types import SimpleNamespace

    from clipwright.schema.task import TaskRequest

    return SimpleNamespace(
        id="task_loop", request=TaskRequest(topic="做一期 3 分钟的数码评测"), plan=None,
        limits=SimpleNamespace(max_turns=10, max_tool_calls=20, max_wall_sec=600,
                               budget_usd=None),
        usage=SimpleNamespace(turns=0, tool_calls=0, wall_sec=0.0, cost_usd=0.0),
    )


def _patch_llm(monkeypatch, fake: _FakeLLM) -> None:
    import clipwright.services.llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLMService", lambda *a, **k: fake)


# ── 工具面 ─────────────────────────────────────────────


def test_agent_tools_are_first_class_surface_tools():
    names = {e.name for e in sf.build_surface()}
    for name in ("delegate_agent", "research_agent", "get_agent_run",
                 "list_agent_runs", "cancel_agent_run"):
        assert name in names, f"{name} 必须是工具面里的一等工具"
    schemas = {s["function"]["name"]: s for s in sf.schemas()}
    assert "delegate_agent" in schemas
    # 档位提示必须在描述里（模型要知道这一步会不会花钱）
    assert "：" in schemas["delegate_agent"]["function"]["description"]


def test_build_tools_contains_schemas_every_round(monkeypatch):
    fake = _FakeLLM([("local_probe", {"n": 1})])
    _patch_llm(monkeypatch, fake)
    asyncio.run(agent_loop.run_loop(_task(), context_text="ctx"))
    assert fake.rounds, "必须真的发起过一次模型请求"
    assert all(len(tools) >= 40 for tools in fake.rounds), "每轮 tools 必须非空"


# ── 执行与回填 ─────────────────────────────────────────


def test_tool_call_is_executed_and_narrated(monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(name, args=None, *, pipeline_id="", idem_key="", dry_run=False,
                           task=None):  # noqa: ANN001, ARG001
        calls.append((name, dict(args or {})))
        return {"status": "ok", "output": {"summary": "探到了 3 个素材"}}

    monkeypatch.setattr(sf, "execute", fake_execute)
    fake = _FakeLLM([("local_probe", {"n": 2})])
    _patch_llm(monkeypatch, fake)

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_loop, "emit",
                        lambda tid, t, m, d=None: events.append({"type": t, "detail": d}))

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert calls == [("local_probe", {"n": 2})]
    assert out["status"] == "ok"
    assert out["tool_calls"][0]["tool"] == "local_probe"
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types


def test_guard_aborts_before_side_effects(monkeypatch):
    executed: list[str] = []

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        executed.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(sf, "execute", fake_execute)
    fake = _FakeLLM([("local_probe", {})])
    _patch_llm(monkeypatch, fake)

    out = asyncio.run(agent_loop.run_loop(_task(), guard=lambda: "预算耗尽"))
    assert out["status"] == "aborted"
    assert out["stopped_reason"] == "预算耗尽"
    assert executed == [], "触闸后不允许再执行任何工具"


def test_tool_call_limit_refuses_instead_of_looping(monkeypatch):
    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        return {"status": "ok"}

    monkeypatch.setattr(sf, "execute", fake_execute)
    # 脚本里排 5 次调用，上限设为 1
    fake = _FakeLLM([("local_probe", {})] * 5)
    _patch_llm(monkeypatch, fake)
    monkeypatch.setattr(agent_loop.settings, "agent_loop_max_tool_calls", 1)

    out = asyncio.run(agent_loop.run_loop(_task()))
    statuses = [c["status"] for c in out["tool_calls"]]
    assert statuses.count("ok") == 1
    assert len(statuses) == 5, "超出上限的调用要如实记为 refused，而不是静默丢"


def test_shadow_mode_runs_no_side_effects(monkeypatch):
    executed: list[str] = []

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        executed.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(sf, "execute", fake_execute)
    fake = _FakeLLM([("local_probe", {}), ("delegate_agent", {"stage": "edit"})])
    _patch_llm(monkeypatch, fake)

    out = asyncio.run(agent_loop.run_loop(_task(), shadow=True))
    assert executed == []
    assert [c["status"] for c in out["tool_calls"]] == ["shadowed", "shadowed"]


# ── 可用性判定（降级必须显式）───────────────────────────


def test_tool_loop_unavailable_without_credential(monkeypatch):
    monkeypatch.setattr(agent_loop.settings, "kernel_mode", "tool_loop")
    monkeypatch.setattr(agent_loop.settings, "llm_api_key", "")
    ok, why = agent_loop.tool_loop_available()
    assert ok is False and why == "no_llm_credential"


def test_tool_loop_unavailable_in_plan_mode(monkeypatch):
    monkeypatch.setattr(agent_loop.settings, "kernel_mode", "plan")
    ok, why = agent_loop.tool_loop_available()
    assert ok is False and "kernel_mode=plan" in why


def test_provider_failure_returns_structured_failure(monkeypatch):
    class _Boom:
        async def with_tools(self, **kwargs):  # noqa: ANN003, ARG002
            raise RuntimeError("provider 挂了")

    _patch_llm(monkeypatch, _Boom())
    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "error"
    assert out["failure"]["category"] == "external_unavailable"
    assert out["failure"]["retryable"] == "retry_same"


def test_prompt_does_not_require_step_array(monkeypatch):
    """可证伪边界：提示词里给的是"初始假设"，不是必须执行的步骤表。"""
    from clipwright.schema.task import Plan, PlanStep

    task = _task()
    task.plan = Plan(rationale="初判", steps=[
        PlanStep(id="s1", capability="structure", title="先出结构")])
    prompt = agent_loop._user_prompt(task, "上下文")
    assert "初始假设" in prompt
    assert "不是必须执行的步骤表" in prompt
    assert json.dumps("steps", ensure_ascii=False) not in prompt


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
