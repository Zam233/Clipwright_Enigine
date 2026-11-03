"""8 类触发点必须真的挂上（PRD §14）——用源码断言 + 事件断言两条腿。"""

from __future__ import annotations

import inspect

from clipwright.services import agent_kernel, agent_loop, ask_user, stage_subagent


def _src(mod) -> str:
    return inspect.getsource(mod)


def test_kernel_has_start_degradation_budget_wrapup():
    src = _src(agent_kernel)
    assert 'trigger="start"' in src
    assert 'trigger="degradation_first"' in src
    assert "on_budget_half(task)" in src
    assert 'trigger="wrapup"' in src


def test_stage_subagent_has_stage_start_and_end():
    src = _src(stage_subagent)
    assert 'trigger="stage_start"' in src
    assert 'trigger="stage_end"' in src


def test_agent_loop_reports_failure_streak():
    src = _src(agent_loop)
    assert 'trigger="tool_failure_streak"' in src
    assert "fail_streak[fingerprint] == 2" in src


def test_ask_user_reports_decision_required():
    src = _src(ask_user)
    assert 'trigger="decision_required"' in src
