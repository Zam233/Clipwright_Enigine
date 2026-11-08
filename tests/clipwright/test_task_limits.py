"""`/proceed` 的任务预算必须真的生效（E2E 2026-10-30：80 轮被截断）。

`TaskRequest` 没有 limits 字段——把 TaskLimits 当 TaskRequest 的 kwarg 传会被静默忽略，
任务仍是默认 80 轮，跑到返工一半反复触发"回合数达到上限"。
"""

from __future__ import annotations

from clipwright.schema.task import TaskLimits, TaskRequest
from clipwright.services.agent_kernel import AgentKernel


def test_create_accepts_limits_override():
    kernel = AgentKernel()
    task = kernel.create(TaskRequest(topic="预算探针", plan_only=False),
                         limits=TaskLimits(max_turns=160, max_tool_calls=800))
    assert task.limits.max_turns == 160
    assert task.limits.max_tool_calls == 800


def test_create_defaults_to_settings_when_limits_absent():
    kernel = AgentKernel()
    task = kernel.create(TaskRequest(topic="默认预算探针", plan_only=False))
    assert task.limits.max_turns > 0
