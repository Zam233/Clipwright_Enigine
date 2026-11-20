"""B13b：`with_tools` 打开共享驱动后由 `agent_loop.drive` 控制循环。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clipwright.config import settings
from clipwright.services.llm import LLMService


def _resp(calls=None, content="", success=True):
    return SimpleNamespace(success=success, status_code=200, content=content,
                           tool_calls=list(calls or []), usage=None)


def _call(name="read_file", args=None, cid="call_1"):
    return SimpleNamespace(id=cid, name=name, arguments=json.dumps(args or {"path": "a"}))


@pytest.mark.asyncio
async def test_with_tools_drive_executes_tool_then_finishes(monkeypatch):
    monkeypatch.setattr(settings, "agent_loop_shared_driver", True)
    svc = LLMService()
    responses = [_resp([_call()], content="先读文件"), _resp([], content="做完了")]
    seen_messages: list[list[dict]] = []

    async def fake_generate(*, messages, **kwargs):
        seen_messages.append([dict(m) for m in messages])
        return responses.pop(0)

    svc.generate = fake_generate  # type: ignore[method-assign]
    executed: list[tuple[str, dict]] = []

    async def executor(name, inp):
        executed.append((name, inp))
        return {"status": "ok", "text": "content"}

    out = await svc.with_tools(
        system_prompt="s", user_prompt="u", tool_executor=executor,
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        max_tool_rounds=3,
    )
    assert out.content == "做完了"
    assert executed == [("read_file", {"path": "a"})]
    assert any(any(m.get("role") == "tool" for m in msgs) for msgs in seen_messages)


@pytest.mark.asyncio
async def test_with_tools_drive_stops_on_llm_failure(monkeypatch):
    monkeypatch.setattr(settings, "agent_loop_shared_driver", True)
    svc = LLMService()
    bad = _resp([], success=False)

    async def fake_generate(*, messages, **kwargs):
        return bad

    svc.generate = fake_generate  # type: ignore[method-assign]
    called = {"n": 0}

    async def executor(name, inp):
        called["n"] += 1
        return {"ok": True}

    out = await svc.with_tools(system_prompt="s", user_prompt="u",
                               tool_executor=executor, tools=[], max_tool_rounds=2)
    assert out is bad and called["n"] == 0
