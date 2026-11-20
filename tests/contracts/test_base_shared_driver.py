"""B13：Base 阶段循环复用公共 `drive`（`agent_loop_shared_driver` 开关）。

关闭时走原 `llm.with_tools` 路径，行为不变；打开时同一策略预算/闸门/事件由
`services.agent_loop.drive` 提供，便于后续阶段统一接入。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from clipwright.agents.base import BaseAgent
from clipwright.services import agent_loop


class _EchoAgent(BaseAgent):
    agent_name = "echo_agent"
    tool_whitelist = ("echo",)

    async def execute(self, input_data, context):  # pragma: no cover - 不测业务
        return input_data


class _FakeResp:
    def __init__(self, calls):
        self.tool_calls = calls
        self.content = ""


class _FakeLLM:
    def __init__(self, seq):
        self.seq = list(seq)
        self.rounds = 0

    async def generate(self, *, messages, tools=None, **kw):
        self.rounds += 1
        return self.seq.pop(0) if self.seq else _FakeResp([])


def _call(name, args=None):
    return SimpleNamespace(name=name, arguments=args or {})


@pytest.mark.asyncio
async def test_shared_driver_runs_tools_until_final(monkeypatch):
    executed: list[tuple[str, dict]] = []

    async def fake_execute(name, **kwargs):
        executed.append((name, kwargs))
        return {"ok": True, "name": name}

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", staticmethod(fake_execute))

    agent = _EchoAgent()
    agent._llm = _FakeLLM([_FakeResp([_call("echo", {"x": 1})]), _FakeResp([])])
    result = await agent._drive_tool_loop(
        system_prompt="s", user_prompt="u",
        schemas=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
        max_rounds=4, max_calls=4, pipeline_id="task_b13",
    )
    assert result["status"] == "ok"
    assert [c["tool"] for c in result["tool_calls"]] == ["echo"]
    assert executed == [("echo", {"x": 1})]


@pytest.mark.asyncio
async def test_shared_driver_stops_at_call_budget(monkeypatch):
    async def fake_execute(name, **kwargs):
        return {"ok": True}

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", staticmethod(fake_execute))
    agent = _EchoAgent()
    agent._llm = _FakeLLM([
        _FakeResp([_call("echo"), _call("echo")]),
        _FakeResp([_call("echo")]),
    ])
    result = await agent._drive_tool_loop(
        system_prompt="s", user_prompt="u",
        schemas=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
        max_rounds=4, max_calls=2, pipeline_id="task_b13",
    )
    assert len(result["tool_calls"]) == 2


@pytest.mark.asyncio
async def test_shared_driver_rejects_non_whitelisted(monkeypatch):
    async def fake_execute(name, **kwargs):
        return {"ok": True}

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", staticmethod(fake_execute))
    agent = _EchoAgent()
    agent._llm = _FakeLLM([_FakeResp([_call("rm_rf", {"path": "/"})]), _FakeResp([])])
    result = await agent._drive_tool_loop(
        system_prompt="s", user_prompt="u",
        schemas=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
        max_rounds=2, max_calls=4, pipeline_id="task_b13",
    )
    assert result["tool_calls"] == []
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_tool_loop_switch_on_uses_drive(monkeypatch):
    from clipwright.config import settings

    executed: list[str] = []

    async def fake_execute(name, **kwargs):
        executed.append(name)
        return {"ok": True}

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", staticmethod(fake_execute))
    monkeypatch.setattr(settings, "agent_loop_shared_driver", True)

    agent = _EchoAgent()
    agent._llm = _FakeLLM([_FakeResp([_call("echo", {"x": 2})]), _FakeResp([])])
    monkeypatch.setattr(agent, "tool_schemas",
                        lambda tools=None: [{"type": "function",
                                             "function": {"name": "echo", "parameters": {}}}])
    out = await agent.run_tool_loop(system_prompt="s", user_prompt="u", max_rounds=3,
                                    max_calls=3, pipeline_id="task_switch")
    assert out["status"] == "ok" and executed == ["echo"]


@pytest.mark.asyncio
async def test_run_tool_loop_switch_off_keeps_legacy_path(monkeypatch):
    from clipwright.config import settings

    called = {"with_tools": 0}

    class _LegacyLLM:
        async def with_tools(self, **kw):
            called["with_tools"] += 1
            return _FakeResp([])

    monkeypatch.setattr(settings, "agent_loop_shared_driver", False)
    agent = _EchoAgent()
    agent._llm = _LegacyLLM()
    monkeypatch.setattr(agent, "tool_schemas",
                        lambda tools=None: [{"type": "function",
                                             "function": {"name": "echo", "parameters": {}}}])
    out = await agent.run_tool_loop(system_prompt="s", user_prompt="u", max_rounds=2,
                                    max_calls=2, pipeline_id="task_legacy")
    assert called["with_tools"] == 1 and out["status"] == "ok"
