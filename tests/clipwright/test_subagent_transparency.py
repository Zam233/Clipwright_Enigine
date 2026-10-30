# -*- coding: utf-8 -*-
"""子 agent 透明化（现代 agent 化，用户 2026-10-29 裁定）。

主 agent 与子 agent 是同一条事件流上的两个会话：子 agent 调了什么工具、失败几次、
跑到第几段，主 agent 必须实时看得见，而不是等最后一句 summary。这组测试锁住：
1. `BaseAgent.call_tool` 的轨迹记账与 `subagent_tool` 事件（含预算封顶）；
2. `agent_context.emit_progress`（无作用域时静默）；
3. `_summary_of` 把失败/降级写进"一句话"；
4. `AgentResult.to_tool_payload` 的轨迹统计、失败优先采样与取全量提示。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.schema.run import AgentResult
from clipwright.services import agent_context
from clipwright.services import stage_subagent


class _Agent:
    """只借用 _note_tool_call / _note_tool_call 依赖的静态方法（不完整实例化 Agent）。"""

    agent_name = "material"

    def __init__(self) -> None:
        self.last_tool_calls: list = []


def _agent():
    from clipwright.agents.base import BaseAgent

    class _Probe(_Agent):
        _note_tool_call = BaseAgent._note_tool_call
        _brief_args = staticmethod(BaseAgent._brief_args)
        _brief_output = staticmethod(BaseAgent._brief_output)

    return _Probe()


def test_tool_call_recorded_with_fields() -> None:
    agent = _agent()
    result = SimpleNamespace(status="success", output={"videos": []}, error="")
    agent._note_tool_call("material_search", "pexels_search", result, 0.0,
                          {"query": "启动器 下载", "limit": 5})
    entry = agent.last_tool_calls[0]
    assert entry["tool"] == "pexels_search"
    assert entry["capability"] == "material_search"
    assert entry["status"] == "success"
    assert "query=启动器 下载" in entry["args_brief"]
    assert entry["output_brief"].startswith("keys=")


def test_tool_call_record_is_bounded() -> None:
    agent = _agent()
    ok = SimpleNamespace(status="success", output=None, error="")
    for i in range(260):
        agent._note_tool_call("c", f"t{i}", ok, 0.0, {})
    assert len(agent.last_tool_calls) == 200
    assert agent.last_tool_calls[-1]["tool"] == "t259"


def test_subagent_tool_event_emitted_inside_task_scope(monkeypatch) -> None:
    agent = _agent()
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)
    ok = SimpleNamespace(status="error", output=None, error="boom")
    with agent_context.task_scope("task_x", stage="material"):
        agent._note_tool_call("video_trim", "ffmpeg_trim", ok, 0.0, {"duration": 3})
        assert agent_context.current_task_id() == "task_x"
        assert agent_context.current_stage() == "material"
    assert emitted and emitted[0][0] == "task_x"
    assert emitted[0][1] == "subagent_tool"
    assert "ffmpeg_trim" in emitted[0][2]
    assert emitted[0][3]["status"] == "error"
    assert agent_context.current_task_id() == ""


def test_no_event_without_scope_but_still_recorded(monkeypatch) -> None:
    agent = _agent()
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)
    ok = SimpleNamespace(status="success", output=None, error="")
    agent._note_tool_call("c", "t", ok, 0.0, {})
    assert emitted == []
    assert len(agent.last_tool_calls) == 1


def test_event_budget_caps_events_but_not_records(monkeypatch) -> None:
    agent = _agent()
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)
    ok = SimpleNamespace(status="success", output=None, error="")
    with agent_context.task_scope("task_b", stage="animation", event_budget=2):
        for i in range(5):
            agent._note_tool_call("c", f"t{i}", ok, 0.0, {})
    assert len(emitted) == 2
    assert len(agent.last_tool_calls) == 5


def test_emit_progress_requires_scope(monkeypatch) -> None:
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)
    assert agent_context.emit_progress("无作用域") is False
    with agent_context.task_scope("task_p", stage="animation"):
        assert agent_context.emit_progress("动画 3/29 完成", {"done": 3}) is True
    assert emitted[0][1] == "subagent_progress"
    assert emitted[0][3]["done"] == 3


def test_summary_includes_failures_and_degradations() -> None:
    summary = stage_subagent._summary_of(
        "material",
        {"candidate_clips": [1] * 43, "degradations": [{"kind": "capability_unavailable"}]},
        tool_calls=[{"status": "success"}, {"status": "error"}, {"status": "success"}],
    )
    assert "候选素材 43 条" in summary
    assert "降级 1 项" in summary and "capability_unavailable" in summary
    assert "工具 3 次（失败 1）" in summary


def test_payload_prefers_failed_and_slow_calls() -> None:
    payload = AgentResult(
        run_id="r", kind="stage", status="ok", stage="edit",
        tool_calls=[
            {"tool": "fast_ok", "status": "success", "duration_ms": 10},
            {"tool": "slow_ok", "status": "success", "duration_ms": 9000},
            {"tool": "broken", "status": "error", "duration_ms": 50, "error": "timeout"},
        ],
    ).to_tool_payload()
    names = [c["tool"] for c in payload["tool_calls"]]
    assert names[0] == "broken"  # 失败优先
    assert "slow_ok" in names[:2]  # 其次最慢
    assert payload["tool_call_stats"]["failed"] == 1
    assert payload["tool_call_stats"]["slowest"]["tool"] == "slow_ok"
    assert "get_agent_run" in payload["tool_calls_hint"]


def test_call_skill_is_recorded_like_a_tool(monkeypatch) -> None:
    """技能调用也必须进轨迹——否则技能成了第二个黑区。"""
    import asyncio

    from clipwright.agents.base import BaseAgent
    from clipwright.schema.skill import SkillExecResult, SkillStatus
    from clipwright.skill import registry as skill_registry

    class _Probe:
        agent_name = "animation"
        capability_prefer: dict = {}

        resolve_skill = BaseAgent.resolve_skill
        call_skill = BaseAgent.call_skill
        _note_tool_call = BaseAgent._note_tool_call
        _note_missing_capability = staticmethod(BaseAgent._note_missing_capability)
        _brief_args = staticmethod(BaseAgent._brief_args)
        _brief_output = staticmethod(BaseAgent._brief_output)

        def __init__(self) -> None:
            self.last_tool_calls = []

    probe = _Probe()
    fake_skill = SimpleNamespace(name="pv_engine", available=True)
    monkeypatch.setattr("clipwright.tool.capabilities.skill_resolve",
                        lambda cap, prefer=(): fake_skill if cap == "pv_audit" else None,
                        raising=False)

    async def _fake_execute(name, **kw):
        return SkillExecResult(status=SkillStatus.SUCCESS, skill_name=name,
                               output={"ok": 1}, duration_ms=5)

    monkeypatch.setattr(skill_registry.SkillRegistry, "execute", _fake_execute, raising=False)

    result = asyncio.run(probe.call_skill("pv_audit", timeline={}))
    assert result.status == SkillStatus.SUCCESS
    assert len(probe.last_tool_calls) == 1
    entry = probe.last_tool_calls[0]
    assert entry["tool"] == "skill:pv_engine"
    assert entry["capability"] == "pv_audit"
    assert entry["status"] == "success"


def test_call_skill_missing_is_recorded_as_failure(monkeypatch) -> None:
    import asyncio

    from clipwright.agents.base import BaseAgent

    class _Probe:
        agent_name = "audio"
        capability_prefer: dict = {}

        resolve_skill = BaseAgent.resolve_skill
        call_skill = BaseAgent.call_skill
        _note_tool_call = BaseAgent._note_tool_call
        _note_missing_capability = staticmethod(BaseAgent._note_missing_capability)
        _brief_args = staticmethod(BaseAgent._brief_args)
        _brief_output = staticmethod(BaseAgent._brief_output)

        def __init__(self) -> None:
            self.last_tool_calls = []

    monkeypatch.setattr("clipwright.tool.capabilities.skill_resolve",
                        lambda cap, prefer=(): None, raising=False)

    probe = _Probe()
    result = asyncio.run(probe.call_skill("no_such_skill"))
    assert str(result.status).lower() == "not_found"
    assert probe.last_tool_calls[0]["tool"] == "skill:no_such_skill"
    assert probe.last_tool_calls[0]["status"] == "not_found"
